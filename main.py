from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response,FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import google.oauth2.id_token
from google.auth.transport import requests
from google.cloud import firestore, storage
from starlette.status import HTTP_400_BAD_REQUEST, HTTP_409_CONFLICT, HTTP_200_OK,HTTP_404_NOT_FOUND
import starlette.status as status
import local_constants
from google.api_core.exceptions import NotFound
from datetime import datetime
import hashlib
import base64


# Define the app with routing
app = FastAPI()

# Firestore client for database interaction
firestore_db = firestore.Client()

# Request object for user login verification via Firebase
firebase_request_adapter = requests.Request()

# Static files and templates setup
app.mount('/static', StaticFiles(directory='static'), name='static')
templates = Jinja2Templates(directory='templates')

#filter to remove the trailing slash
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


templates.env.filters['trim_trailing_slash'] = trim_trailing_slash
templates.env.filters['parent_directory'] = parent_directory


#path bug fix
def normalize_path(path):
    #Remove any redundant slashes from the path
    while '//' in path:
        path = path.replace('//', '/')
    return path.strip('/')


def addDirectory(directory_name, current_path=""):
    try:
        # Creating path with slash
        if not current_path.endswith('/'):
            current_path += '/'
        full_path = f"{current_path}{directory_name}/"

        # Firestore and Cloud Storage clients
        storage_client = storage.Client(project=local_constants.PROJECT_NAME)
        bucket = storage_client.bucket(local_constants.PROJECT_STORAGE_BUCKET)
        
        # Unique directory id for Firestore (temp)
        dir_id = firestore_db.collection('directories').document().id
        blob = bucket.blob(full_path)
        blob.upload_from_string('', content_type='application/x-www-form-urlencoded;charset=UTF-8')

        # Add the directory data to Firestore 
        dir_data = {
            'id': dir_id,  
            'path': full_path,
            'parent_directory_id': None,
            'subdirectories': [],
            'files': []
        }
        dir_ref = firestore_db.collection('directories').document(dir_id)
        dir_ref.set(dir_data)
        
        return {"message": "Directory created successfully", "directory_id": dir_id, "directory_data": dir_data}
    except Exception as e:
        return {"error": str(e), "status": 500}





async def addFile(file, full_path, overwrite=False):
    try:
        storage_client = storage.Client(project=local_constants.PROJECT_NAME)
        bucket = storage_client.bucket(local_constants.PROJECT_STORAGE_BUCKET)
        blob = bucket.blob(full_path)

        # Read file content
        file_content = await file.read()
        await file.seek(0)  # Reset the file pointer if needed

        # Check for duplicates in the current directory by comparing MD5 hashes
        directory_path = '/'.join(full_path.split('/')[:-1]) + '/'
        blobs = bucket.list_blobs(prefix=directory_path)

        # Get the MD5 hash of the file to be uploaded
        local_md5_hash = hashlib.md5(file_content).hexdigest()
        duplicate_files = []

        for existing_blob in blobs:
            # Blob's md5_hash is base64 encoded, so decode it first
            existing_md5_hash = base64.b64decode(existing_blob.md5_hash).hex()
            if existing_md5_hash == local_md5_hash:
                duplicate_files.append(existing_blob.name)

        if duplicate_files:
            return {"error": "Duplicate file detected based on content.", "status": 409, "duplicate_files": duplicate_files}

        # If no duplicates, upload the file
        blob.upload_from_string(file_content, content_type=file.content_type)

        # Add file metadata to Firestore including the MD5 hash
        file_data = {
            'name': blob.name,
            'path': full_path,
            'content_type': blob.content_type,
            'size': blob.size,
            'created_at': datetime.now().isoformat(),
            'hash': local_md5_hash  # Store the MD5 hash
        }
        firestore_db.collection('files').add(file_data)

        return {"message": "File uploaded successfully", "status": 200}
    except Exception as e:
        return {"error": str(e), "status": 400}



def getUser(user_token):
    user_id = user_token['user_id']
    user_doc_ref = firestore_db.collection('users').document(user_id)
    user_doc = user_doc_ref.get()

    if not user_doc.exists:
        try:
            # Setup initial user data
            user_data = {
                'name': user_token.get('name', 'New User'),
                'email': user_token.get('email', 'no-email@example.com'),
                'root_directory': None
            }

            # Create root directory in Firestore
            root_dir_ref = firestore_db.collection('directories').document()
            root_directory_data = {
                'path': '/',
                'parent_directory_id': None,
                'subdirectories': [],
                'files': [],
                'created_at': datetime.now().isoformat()
            }
            root_dir_ref.set(root_directory_data)

            # Update user data with root directory reference
            user_data['root_directory'] = root_dir_ref.id
            user_doc_ref.set(user_data)

            # Complete user data including root directory
            user_data['id'] = user_id
            return user_data
        except Exception as e:
            return {"error": str(e), "status": 500}
    else:
        return user_doc.to_dict()

    
# Downloads the contents of a blob by filename.
def downloadBlob(filename):
    storage_client = storage.Client(project=local_constants.PROJECT_NAME)
    bucket = storage_client.bucket(local_constants.PROJECT_STORAGE_BUCKET)
    blob = bucket.get_blob(filename)
    return blob.download_as_bytes()




# Function to validate a Firebase token and return the user_token if valid
def validateFirebaseToken(id_token):
    if not id_token:
        return None
    user_token = None
    try:
        user_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request_adapter)
    except ValueError as err:
        print(str(err))  # test
    return user_token

#temp favicon in order to display directo..
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse('static/favicon.ico')


@app.post("/add-directory", response_class=RedirectResponse)
async def addDirectoryHandler(request: Request):
    form = await request.form()
    dir_name = form.get('dir_name')
    current_directory = form.get('current_directory', '')

    if not dir_name:
        return RedirectResponse(url='/', status_code=status.HTTP_303_SEE_OTHER)

    # Add the directory and redirect to the new directory view
    addDirectory(dir_name, current_directory)
    return RedirectResponse(url=f'{current_directory}{dir_name}/', status_code=status.HTTP_303_SEE_OTHER)

@app.post("/download-file", response_class=Response)
async def downloadFileHandler(request: Request):
    # Validate the user's token 
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)
    if not user_token:
        return RedirectResponse('/')
    
    # Retrieve filename from form data for download
    form = await request.form()
    filename = form.get('filename')  
    if not filename:
        raise HTTPException(status_code=400, detail="Filename not provided")

    file_content = downloadBlob(filename)
    headers = {
        "Content-Disposition": f"attachment; filename={filename}"
    }
    return Response(content=file_content, media_type="application/octet-stream", headers=headers)


@app.post("/upload-file", response_class=JSONResponse)
async def uploadFileHandler(request: Request):
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)
    if not user_token:
        return RedirectResponse('/', status_code=HTTP_400_BAD_REQUEST)

    form = await request.form()
    file = form.get('file_name')
    overwrite = form.get('overwrite', 'false').lower() == 'true'  
    current_directory = form.get('current_directory', '/')

    if not file or not hasattr(file, 'filename') or not file.filename:
        return JSONResponse(status_code=HTTP_400_BAD_REQUEST, content={"error": "No file provided"})

    # Ensure the directory path ends with a '/'
    if not current_directory.endswith('/'):
        current_directory += '/'

    full_path = f"{current_directory}{file.filename}"

    # Correct use of await with the asynchronous addFile function
    result = await addFile(file, full_path, overwrite)
    if result['status'] == HTTP_409_CONFLICT:
        return JSONResponse(status_code=HTTP_409_CONFLICT, content={"error": result["error"]})
    elif result['status'] == HTTP_200_OK:
        return JSONResponse(status_code=HTTP_200_OK, content={"message": result["message"]})
    else:
        # Handle unexpected results
        return JSONResponse(status_code=HTTP_400_BAD_REQUEST, content={"error": "An error occurred during the file upload"})




# Function to delete a file from the storage bucket
def deleteFile(filename):
    storage_client = storage.Client(project=local_constants.PROJECT_NAME)
    bucket = storage_client.bucket(local_constants.PROJECT_STORAGE_BUCKET)
    blob = bucket.blob(filename)
    blob.delete()
    print(f"Deleted {filename}")

@app.post("/delete-file", response_class=RedirectResponse)
async def deleteFileHandler(request: Request):
    # Validate the user's token
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)
    if not user_token:
        return RedirectResponse('/')

    # Retrieve filename from form data for deletion
    form = await request.form()
    filename = form.get('filename')  
    if not filename:
        raise HTTPException(status_code=400, detail="Filename not provided")

   
    deleteFile(filename)
    return RedirectResponse('/', status_code=status.HTTP_302_FOUND)

@app.get(" ", response_class=HTMLResponse)
async def view_root_directory(request: Request):
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)
    if not user_token:
        return RedirectResponse('/')
    return await view_directory(request, dir_name='')

@app.get("{dir_name:path}", response_class=HTMLResponse)
async def view_directory(request: Request, dir_name: str):
    token = request.cookies.get("token")
    user_token = validateFirebaseToken(token)
    # if not user_token: 
    #     return RedirectResponse('/') bugged out and was infinitly redirecting 

    if not dir_name.endswith('/'):
        dir_name += '/'

    print("Directory being viewed:", dir_name)

    if "favicon.ico" in dir_name:
         return RedirectResponse('/')

    # Get the list of files and subdirectories
    blobs, subdirectories = blobList(dir_name)
    blobs_list = list(blobs)  # Files in the current directory

    # Separate the blobs into files and directories based on whether their names end with '/'
    file_list = [blob.name for blob in blobs if not blob.name.endswith('/')]
    directory_list = [prefix for prefix in subdirectories]

    user_info = getUser(user_token) if user_token else None
    error_message = "This directory is empty." if not file_list and not directory_list else None

    return templates.TemplateResponse('main.html', {
        'request': request,
        'directory_name': dir_name,  
        'file_list': file_list,
        'directory_list': directory_list,
        'user_info': user_info,
        'error_message': error_message
    })

# Function to delete a directory
def deleteDirectory(directory_path):
    storage_client = storage.Client(project=local_constants.PROJECT_NAME)
    bucket = storage_client.bucket(local_constants.PROJECT_STORAGE_BUCKET)
    
    # Normalize the path to ensure it ends with a '/'
    if not directory_path.endswith('/'):
        directory_path += '/'
    
    # Get the Firestore document reference
    dirs_collection = firestore_db.collection('directories')
    dir_docs = dirs_collection.where('path', '==', directory_path).stream()

    dir_doc = next(dir_docs, None)
    if not dir_doc:
        return {"error": "Directory not found", "status": HTTP_404_NOT_FOUND}

    # Check for existing files and subdirectories
    blobs = list(bucket.list_blobs(prefix=directory_path))
    if any(blob.name != directory_path for blob in blobs):  
        return {"error": "Directory is not empty", "status": HTTP_409_CONFLICT, "message": "Directory is not empty. Please remove all contents first."}

    # If directory is empty, proceed with deletion
    for blob in blobs:
        try:
            blob.delete()
        except NotFound:
            continue  
    
    # Delete the directory document from Firestore
    dir_doc.reference.delete()

    return {"message": "Directory deleted successfully", "status": HTTP_200_OK}



@app.post("/delete-directory", response_class=JSONResponse)
async def deleteDirectoryHandler(request: Request):
    form = await request.form()
    directory_path = form.get('directory_path')
    if not directory_path:
        return JSONResponse(status_code=HTTP_400_BAD_REQUEST, content={"error": "Directory path not provided"})

    result = deleteDirectory(directory_path)
    if "error" in result:
        return JSONResponse(status_code=result["status"], content={"error": result["error"], "message": result.get("message", "An error occurred")})
    return JSONResponse(status_code=result["status"], content={"message": result["message"]})




# Returns a list of blobs in the bucket filtered by the prefix
def blobList(prefix="", delimiter="/"):
    storage_client = storage.Client(project=local_constants.PROJECT_NAME)
    if prefix and not prefix.endswith(delimiter):
        prefix += delimiter

    iterator = storage_client.list_blobs(
        local_constants.PROJECT_STORAGE_BUCKET, prefix=prefix, delimiter=delimiter
    )

    blobs_list = list(iterator)
    subdirectory_prefixes = set(iterator.prefixes) if hasattr(iterator, 'prefixes') else set()

    if prefix == "":
        subdirectory_prefixes.discard(delimiter)

    return blobs_list, subdirectory_prefixes




@app.get('/', response_class=HTMLResponse)
async def root(request: Request):
    token = request.cookies.get("token")
    user_token = validateFirebaseToken(token)
    
    if not user_token:
        return templates.TemplateResponse('main.html', {
            'request': request, 
            'error_message': "No error here", 
            'user_info': None
        })
    
    # Call blobList without a prefix to get the blobs in the root directory
    blobs, subdirectory_prefixes = blobList()
    print("Blobs at root:", blobs)
    print("Subdirectories at root:", subdirectory_prefixes)

    file_list = [blob.name for blob in blobs if not blob.name.endswith('/')]
    directory_list = list(subdirectory_prefixes)
    print("File list:", file_list)
    print("Directory list:", directory_list)
    
    user_info = getUser(user_token) if user_token else None

    return templates.TemplateResponse('main.html', {
        'request': request, 
        'error_message': "No error here", 
        'user_info': user_info, 
        'file_list': file_list, 
        'directory_list': directory_list
    })