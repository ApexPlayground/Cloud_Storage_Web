from fastapi import FastAPI, Request, HTTPException,Path
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import google.oauth2.id_token
from google.auth.transport import requests
from google.cloud import firestore, storage
import starlette.status as status
import local_constants
from datetime import datetime

# Define the app with routing
app = FastAPI()

# Firestore client for database interaction
firestore_db = firestore.Client()

# Request object for user login verification via Firebase
firebase_request_adapter = requests.Request()

# Static files and templates setup
app.mount('/static', StaticFiles(directory='static'), name='static')
templates = Jinja2Templates(directory='templates')

# Define a filter to remove the trailing slash
def trim_trailing_slash(path):
    return path.rstrip('/')

def parent_directory(path):
   
    path = path.strip('/')
    if not path:
        
        return ''
    parts = path.split('/')
    if len(parts) > 1:
        return '/'.join(parts[:-1]) + '/'
    else:
        
        return ''







# Register filters with the Jinja environment
templates.env.filters['trim_trailing_slash'] = trim_trailing_slash
templates.env.filters['parent_directory'] = parent_directory

#function to add directory to buket & firestore 
def addDirectory(directory_name, current_path=""):
    # creating path with slash
    if not current_path.endswith('/'):
        current_path += '/'
    full_path = f"{current_path}{directory_name}/" 

    storage_client = storage.Client(project=local_constants.PROJECT_NAME)
    bucket = storage_client.bucket(local_constants.PROJECT_STORAGE_BUCKET)
    blob = bucket.blob(full_path)
    blob.upload_from_string('', content_type='application/x-www-form-urlencoded;charset=UTF-8')

    # Add the directory data to Firestore under the new full_path
    dir_data = {
        'path': full_path,
        'parent_directory_id': None,
        'subdirectories': [],
        'files': []
    }
    dir_ref = firestore_db.collection('directories').document()
    dir_ref.set(dir_data)
    return dir_ref.id


# Adds a file to the storage bucket
def addFile(file, current_path=""):
    # Construct the full path where the file will be uploaded
    full_path = f"{current_path}{file.filename}" if current_path else file.filename
    
    storage_client = storage.Client(project=local_constants.PROJECT_NAME)
    bucket = storage_client.bucket(local_constants.PROJECT_STORAGE_BUCKET)
    blob = storage.Blob(full_path, bucket)
    blob.upload_from_file(file.file)


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




def getUser(user_token):
    user_id = user_token['user_id']
    user_doc_ref = firestore_db.collection('users').document(user_id)
    user_doc = user_doc_ref.get()

    if not user_doc.exists:
        # User data setup
        user_data = {
            'name': user_token.get('name', 'New User'), 
            'email': user_token.get('email', 'no-email@example.com'),  
            'root_directory': None 
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

# Root function
@app.get('/', response_class=HTMLResponse)
async def root(request: Request):
    token = request.cookies.get("token")
    error_message = "No error here"
    user_token = validateFirebaseToken(token)
    
    # Check what user_token contains
    print("User Token:", user_token)
    
    if not user_token:
        return templates.TemplateResponse('main.html', {'request': request, 'error_message': error_message, 'user_info': None})
    
    file_list = []
    directory_list = []
    
    blobs = blobList(None) 
    for blob in blobs:
        if blob.name[-1] == '/':
            directory_list.append(blob)
        else:
            file_list.append(blob)
    
    user_info = getUser(user_token) if user_token else None
    # Check what user_info contains
    print("User Info:", user_info)

    return templates.TemplateResponse('main.html', {'request': request, 'error_message': error_message, 'user_info': user_info, 'file_list': file_list, 'directory_list': directory_list})



@app.post("/add-directory", response_class=RedirectResponse)
async def addDirectoryHandler(request: Request):
    form = await request.form()
    dir_name = form.get('dir_name')
    current_directory = form.get('current_directory', '')

    if not dir_name:
        return RedirectResponse(url='/', status_code=status.HTTP_303_SEE_OTHER)

    # Add the directory and redirect to the new directory view
    addDirectory(dir_name, current_directory)
    return RedirectResponse(url=f'/directory/{current_directory}{dir_name}/', status_code=status.HTTP_303_SEE_OTHER)

@app.post("/download-file", response_class=Response)
async def downloadFileHandler(request: Request):
    # Validate the user's token from cookies, redirect if invalid
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)
    if not user_token:
        return RedirectResponse('/')
    
    # Retrieve filename from form data for downloadm
    form = await request.form()
    filename = form.get('filename')  
    if not filename:
        raise HTTPException(status_code=400, detail="Filename not provided")

    file_content = downloadBlob(filename)
    headers = {
        "Content-Disposition": f"attachment; filename={filename}"
    }
    return Response(content=file_content, media_type="application/octet-stream", headers=headers)


# function to upload file to buket 
@app.post("/upload-file", response_class=RedirectResponse)
async def uploadFileHandler(request: Request):
    form = await request.form()
    file = form['file_name']
    current_directory = form.get('current_directory', '')
    
    if not file.filename:
        return RedirectResponse('/', status_code=status.HTTP_302_FOUND)

    if not current_directory.endswith('/'):
        current_directory += '/'
    full_path = f"{current_directory}{file.filename}"  # Full path construction

    addFile(file, full_path)
    return RedirectResponse(url=f'/directory/{current_directory}', status_code=status.HTTP_302_FOUND)


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
    filename = form.get('filename') 
    if not filename:
        raise HTTPException(status_code=400, detail="Filename not provided")

    # Call the delete function
    deleteFile(filename)
    return RedirectResponse('/', status_code=status.HTTP_302_FOUND)

@app.get("/directory/", response_class=HTMLResponse)
async def view_root_directory(request: Request):
    # Redirect to the true root
    return RedirectResponse(url='/')

@app.get("/directory/{dir_name:path}", response_class=HTMLResponse)
async def view_directory(request: Request, dir_name: str = ''):
    token = request.cookies.get("token")
    user_token = validateFirebaseToken(token)
    if not user_token:
        return RedirectResponse('/')

    # Normalize the directory name to handle root directory
    dir_name = dir_name.strip('/')
    full_dir_name = f'/{dir_name}' if dir_name else '/'  # This creates a path with a leading slash

    blobs = blobList(dir_name if dir_name else None)  # Call with None if root
    blobs_list = list(blobs)

    file_list = [blob.name for blob in blobs_list if not blob.name.endswith('/')]
    directory_list = [blob.name for blob in blobs_list if blob.name.endswith('/')]

    user_info = getUser(user_token) if user_token else None
    error_message = "This directory is empty." if not file_list and not directory_list else None

    # Calculate the parent directory path for navigation purposes
    parent_path = parent_directory(full_dir_name) if dir_name else None  # Calculate only if not root

    return templates.TemplateResponse('main.html', {
        'request': request,
        'directory_name': full_dir_name,  # Use the normalized full path
        'file_list': file_list,
        'directory_list': directory_list,
        'user_info': user_info,
        'error_message': error_message,
        'parent_path': parent_path  # Only pass if not root
    })


