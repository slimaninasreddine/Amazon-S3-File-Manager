#app.py
from flask import Flask, jsonify, send_file, request, after_this_request
from flask_cors import CORS
import boto3
import os
import tempfile
from botocore.exceptions import ClientError
from boto3.s3.transfer import TransferConfig
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from werkzeug.datastructures import Headers
import logging

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app)

# AWS Configuration
AWS_ACCESS_KEY = os.getenv('AWS_ACCESS_KEY')
AWS_SECRET_KEY = os.getenv('AWS_SECRET_KEY')
AWS_REGION = os.getenv('AWS_REGION')
S3_BUCKET = os.getenv('S3_BUCKET')

# Initialize S3 client
s3_client = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY,
    region_name=AWS_REGION
)


@app.route('/')
def index():
    return send_file('static/index.html')

@app.route('/api/list', methods=['GET'])
def list_objects():
    try:
        prefix = request.args.get('prefix', '')
        
        params = {
            'Bucket': S3_BUCKET,
            'Prefix': prefix,
            'Delimiter': '/'
        }
        
        response = s3_client.list_objects_v2(**params)

        folders = [{'name': p['Prefix'].rstrip('/').split('/')[-1], 
                    'path': p['Prefix']} 
                   for p in response.get('CommonPrefixes', [])]
        
        files = [{'name': obj['Key'].split('/')[-1],
                  'path': obj['Key'],
                  'size': obj['Size'],
                  'lastModified': obj['LastModified'].isoformat()}
                 for obj in response.get('Contents', [])
                 if not obj['Key'].endswith('/')]

        return jsonify({
            'folders': folders,
            'files': files
        })
    
    except Exception as e:
        return jsonify({'error': 'Failed to load directory: ' + str(e)}), 500

@app.route('/api/upload', methods=['POST'])
def upload_file():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        prefix = request.form.get('prefix', '')
        
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        filename = secure_filename(file.filename)
        file_path = os.path.join(prefix, filename)

        s3_client.upload_fileobj(file, S3_BUCKET, file_path)
        
        return jsonify({'message': 'File uploaded successfully', 'path': file_path})
    
    except Exception as e:
        return jsonify({'error': 'Failed to upload file: ' + str(e)}), 500

@app.route('/api/download', methods=['GET'])
def download_file():
    tmp_file = None
    try:
        file_path = request.args.get('path')
        if not file_path:
            return jsonify({'error': 'No file path provided'}), 400
        
        config = TransferConfig(
            multipart_threshold=8 * 1024 * 1024,
            max_concurrency=10,
            multipart_chunksize=8 * 1024 * 1024,
            use_threads=True
        )
        
        tmp_file = tempfile.NamedTemporaryFile(delete=False)
        s3_client.download_fileobj(S3_BUCKET, file_path, tmp_file, Config=config)
        tmp_file.close()

        file_name = file_path.split('/')[-1]
        
        @after_this_request
        def cleanup(response):
            if os.path.exists(tmp_file.name):
                try:
                    os.unlink(tmp_file.name)
                except Exception as e:
                    app.logger.error(f"Failed to delete temp file: {e}")
            return response

        response = send_file(
            tmp_file.name,
            as_attachment=True,
            download_name=file_name,
            conditional=True,
            mimetype='application/octet-stream'
        )

        response.headers['Cache-Control'] = 'no-cache'

        return response
    
    except Exception as e:
        if tmp_file and os.path.exists(tmp_file.name):
            try:
                os.unlink(tmp_file.name)
            except Exception as delete_error:
                app.logger.error(f"Failed to delete temp file: {delete_error}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/delete', methods=['DELETE'])
def delete_object():
    try:
        path = request.args.get('path')
        if not path:
            return jsonify({'error': 'No path provided'}), 400
        
        if path.endswith('/'):  # Delete folder and contents
            paginator = s3_client.get_paginator('list_objects_v2')
            objects_to_delete = []
            
            for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=path):
                if 'Contents' in page:
                    objects_to_delete.extend(
                        [{'Key': obj['Key']} for obj in page['Contents']]
                    )
            
            if objects_to_delete:
                s3_client.delete_objects(
                    Bucket=S3_BUCKET,
                    Delete={'Objects': objects_to_delete}
                )
        else:  # Delete single file
            s3_client.delete_object(Bucket=S3_BUCKET, Key=path)
        
        return jsonify({'message': 'Deleted successfully'})
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)