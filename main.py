from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import google.oauth2.id_token
from google.auth.transport import requests
from google.cloud import firestore, storage
import starlette.status as status
import local_constants

# Define the app with routing
app = FastAPI()

# Firestore client for database interaction
firestore_db = firestore.Client()

# Request object for user login verification via Firebase
firebase_request_adapter = requests.Request()

# Static files and templates setup
app.mount('/static', StaticFiles(directory='static'), name='static')
templates = Jinja2Templates(directory='templates')

def add_directory(bucket_name, directory_name):
    """Ensure a directory exists in GCS by creating a placeholder."""
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    if not directory_name.endswith('/'):
        directory_name += '/'
    blob = bucket.blob(directory_name)
    blob.upload_from_string('')  # Upload empty content to create the directory


# Adds a file to the storage bucket
def addFile(file, directory_id):
    # Adds a file to the storage bucket
    storage_client = storage.Client(project=local_constants.PROJECT_NAME)
    bucket = storage_client.bucket(local_constants.PROJECT_STORAGE_BUCKET)
    blob = storage.Blob(file.filename, bucket)
    blob.upload_from_file(file.file)

    # Add file metadata to Firestore
    file_metadata = {
        'file_name': file.filename,
        'directory_id': directory_id,
        'size': blob.size,
        'content_type': blob.content_type,
        'created_at': datetime.now().isoformat()
    }
    file_ref = firestore_db.collection('files').document()
    file_ref.set(file_metadata)
    return file_ref.id


# Returns a list of blobs in the bucket filtered by the prefix
def blobList(prefix=""):
    storage_client = storage.Client(project=local_constants.PROJECT_NAME)
    return storage_client.list_blobs(local_constants.PROJECT_STORAGE_BUCKET, prefix=prefix)


# Downloads the contents of a blob by filename.
def downloadBlob(filename):
    storage_client = storage.Client(project=local_constants.PROJECT_NAME)
    bucket = storage_client.bucket(local_constants.PROJECT_STORAGE_BUCKET)
    blob = bucket.get_blob(filename)
    return blob.download_as_bytes()


from datetime import datetime

def getUser(user_token):
    user_id = user_token['user_id']
    user_doc_ref = firestore_db.collection('users').document(user_id)
    user_doc = user_doc_ref.get()

    if not user_doc.exists:
        # User data setup
        user_data = {
            'name': user_token.get('name', 'New User'),  # Assume token contains name
            'email': user_token.get('email', 'no-email@example.com'),  # Assume token contains email
            'root_directory': None  # This will be set after creating the directory
        }

        # Create root directory in Firestore
        root_directory_data = {
            'path': '/',
            'parent_directory_id': None,  # No parent for root
            'subdirectories': [],
            'files': [],
            'created_at': datetime.now().isoformat()
        }
        root_dir_ref = firestore_db.collection('directories').document()
        root_dir_ref.set(root_directory_data)

        # Update user data with root directory reference
        user_data['root_directory'] = root_dir_ref.id
        user_doc_ref.set(user_data)

        # Return complete user data including root directory
        user_data['id'] = user_id
        return user_data
    else:
        # Return existing user data
        return user_doc.to_dict()



# Function to validate a Firebase token and return the user_token if valid
def validateFirebaseToken(id_token):
    if not id_token:
        return None
    user_token = None
    try:
        user_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request_adapter)
    except ValueError as err:
        print(str(err))  # Log the error for debugging
    return user_token

@app.get('/', response_class=HTMLResponse)
async def root(request: Request):
    token = request.cookies.get("token")
    error_message = "No error here"
    user_token = validateFirebaseToken(token)
    
    if not user_token:
        return templates.TemplateResponse('main.html', {'request': request, 'error_message': error_message, 'user_info': None, 'file_list': [], 'directory_list': []})
    
    file_list = []
    directory_list = []
    
    blobs = blobList(None) 
    for blob in blobs:
        if blob.name[-1] == '/':
            directory_list.append(blob)
        else:
            file_list.append(blob)
    
    user_info = getUser(user_token) if user_token else None

    # Fetch directory contents for the root directory
    root_contents = list_directory_contents(local_constants.PROJECT_STORAGE_BUCKET)
    
    # Render the main page template with directory contents included
    return templates.TemplateResponse('main.html', {
        'request': request,
        'error_message': error_message,
        'user_info': user_info,
        'file_list': file_list,
        'directory_list': directory_list,
        'directory_contents': root_contents  # Pass directory contents to template
    })




@app.post("/add-directory", response_class=RedirectResponse)
async def add_directory_handler(request: Request):
    form = await request.form()
    directory_name = form.get('dir_name')
    if not directory_name:
        raise HTTPException(status_code=400, detail="Directory name is required")

    add_directory(local_constants.PROJECT_STORAGE_BUCKET, directory_name)

    return RedirectResponse(url=f"/directory/{directory_name}", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/download-file", response_class=Response)
async def downloadFileHandler(request: Request):
    # Validate the user's token from cookies, redirect if invalid
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)
    if not user_token:
        return RedirectResponse('/')
    
    # Retrieve filename from form data for download
    form = await request.form()
    filename = form.get('filename')  # Use .get() to avoid KeyError
    if not filename:
        raise HTTPException(status_code=400, detail="Filename not provided")

    file_content = downloadBlob(filename)
    headers = {
        "Content-Disposition": f"attachment; filename={filename}"
    }
    return Response(content=file_content, media_type="application/octet-stream", headers=headers)


@app.post("/upload-file", response_class=RedirectResponse)
async def uploadFileHandler(request: Request):
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)
    if not user_token:
        return RedirectResponse('/')

    form = await request.form()
    file = form['file_name']
    if not file.filename:
        return RedirectResponse('/', status_code=status.HTTP_302_FOUND)

    # Retrieve the current directory ID from the session or a predetermined root directory ID
    current_directory_id = request.session.get('current_directory_id', 'root_directory_id')
    addFile(file, current_directory_id)

    return RedirectResponse(f'/directory/{current_directory_id}', status_code=status.HTTP_302_FOUND)


# Function to delete a file from the storage bucket
def deleteFile(filename):
    storage_client = storage.Client(project=local_constants.PROJECT_NAME)
    bucket = storage_client.bucket(local_constants.PROJECT_STORAGE_BUCKET)
    blob = bucket.blob(filename)
    blob.delete()
    print(f"Deleted {filename}")

@app.post("/delete-file", response_class=RedirectResponse)
async def deleteFileHandler(request: Request):
    # Validate the user's token from cookies, redirect if invalid
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)
    if not user_token:
        return RedirectResponse('/')

    # Retrieve filename from form data for deletion
    form = await request.form()
    filename = form.get('filename')  # Use .get() to avoid KeyError
    if not filename:
        raise HTTPException(status_code=400, detail="Filename not provided")

    # Call the delete function
    deleteFile(filename)
    return RedirectResponse('/', status_code=status.HTTP_302_FOUND)

def list_directory_contents(bucket_name, prefix=''):
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    iterator = storage_client.list_blobs(bucket, prefix=prefix, delimiter='/')

    files = []
    directories = []
    for page in iterator.pages:
        files.extend([blob.name for blob in page])
        directories.extend(page.prefixes)

    return {'files': files, 'directories': directories}

@app.get("/directory/{dir_name:path}", response_class=HTMLResponse)
async def view_directory(request: Request, dir_name: str = ""):
    # Normalize the directory name to ensure it ends with '/'
    if not dir_name.endswith('/'):
        dir_name += '/'
    
    # Fetch directory contents
    contents = list_directory_contents(local_constants.PROJECT_STORAGE_BUCKET, dir_name)
    
    # Render directory contents using your template
    return templates.TemplateResponse('directory_view.html', {
        'request': request,
        'directory_name': dir_name,
        'files': contents['files'],
        'directories': contents['directories']
    })




