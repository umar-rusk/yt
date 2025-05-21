import os
import shutil
import zipfile
import json # Added for X-Download-Errors header
from flask import Flask, request, send_file, jsonify, render_template, make_response
import yt_dlp

app = Flask(__name__)

DOWNLOADS_DIR = 'downloads'

# Ensure the downloads directory exists
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

class YtdlpLogger:
    def debug(self, msg):
        # For compatibility with youtube-dl, both debug and info are passed into debug
        # You can distinguish them by the prefix '[debug] '
        if msg.startswith('[debug] ') and 'Traceback' not in msg: # Filter out full tracebacks from debug
             app.logger.debug(msg) # Log yt-dlp debug messages to Flask logger
        # else: # Info messages are also passed here
            # app.logger.info(msg) # Optionally log info messages
            pass

    def info(self, msg):
        # app.logger.info(msg) # Optionally log info messages
        pass

    def warning(self, msg):
        app.logger.warning(msg)

    def error(self, msg):
        app.logger.error(msg)


@app.route('/')
def index():
    """Serves the main HTML page."""
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def download_videos():
    """Handles video download requests."""
    data = request.get_json()
    if not data or 'urls' not in data:
        return jsonify({'status': 'error', 'errors': [{'url': 'N/A', 'error': 'No URLs provided in request'}]}), 400

    urls = data['urls']
    if not isinstance(urls, list) or not urls:
        return jsonify({'status': 'error', 'errors': [{'url': 'N/A', 'error': 'URLs must be a non-empty list'}]}), 400

    # Clear previous downloads
    if os.path.exists(DOWNLOADS_DIR):
        shutil.rmtree(DOWNLOADS_DIR)
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)

    ydl_opts_base = {
        'format': 'bestvideo[height<=1080]+bestaudio/bestvideo*+bestaudio/best',
        'outtmpl': os.path.join(DOWNLOADS_DIR, '%(title)s [%(id)s].%(ext)s'),
        'noplaylist': True,
        'logger': YtdlpLogger(), # Capture yt-dlp logs
        # 'quiet': False, # Ensure logs are produced for the logger
        # 'no_warnings': False,
    }

    downloaded_files_info = [] # Store dicts with path and original url
    download_errors = []

    for url in urls:
        if not url.strip():
            download_errors.append({'url': url, 'error': 'Empty URL string provided.'})
            continue
        
        current_ydl_opts = ydl_opts_base.copy()
        # current_ydl_opts['outtmpl'] = os.path.join(DOWNLOADS_DIR, f'%(title)s [%(id)s]_{url_to_filename(url)}.%(ext)s')
        
        try:
            filename_from_info = ""
            with yt_dlp.YoutubeDL(current_ydl_opts) as ydl:
                # Try to extract info first to get a title for error messages and a potential filename
                try:
                    info = ydl.extract_info(url, download=False)
                    filename_from_info = ydl.prepare_filename(info)
                    # Ensure the filename is within the DOWNLOADS_DIR
                    if not filename_from_info.startswith(DOWNLOADS_DIR):
                         filename_from_info = os.path.join(DOWNLOADS_DIR, os.path.basename(filename_from_info))
                except Exception as info_e:
                    # If info extraction fails, we might not have a title, but can still try downloading
                    app.logger.warning(f"Could not extract info for {url} before download: {info_e}")
                    # Fallback filename part if title extraction fails before download attempt
                    # The actual downloaded filename will be caught later.
                    filename_from_info = os.path.join(DOWNLOADS_DIR, f"download_for_{url.split('/')[-1] or 'unknown_url'}")


                app.logger.info(f"Attempting download for URL: {url}")
                error_code = ydl.download([url]) # Perform actual download

                # Determine the actual filename after download
                # This is crucial because prepare_filename might not perfectly match final name
                # especially after sanitization or if title changes.
                
                actual_downloaded_path = None
                if error_code == 0: # Success
                    # Find the file. Best effort.
                    # If info was extracted, filename_from_info is our best guess.
                    if os.path.exists(filename_from_info):
                        actual_downloaded_path = filename_from_info
                    elif info and info.get('id'): # Fallback: search by ID in filename
                         for f_name in os.listdir(DOWNLOADS_DIR):
                            if info['id'] in f_name:
                                actual_downloaded_path = os.path.join(DOWNLOADS_DIR, f_name)
                                break
                    else: # Fallback: if only one file in downloads dir, assume it's this one
                        # This is less reliable if multiple downloads happen rapidly and one fails to be found by ID
                        # For single URL processing, this is safer.
                        # With multiple URLs, this part of logic might misattribute if not careful.
                        # Current loop processes one URL at a time, so listdir should be okay.
                        potential_files = [os.path.join(DOWNLOADS_DIR, f) for f in os.listdir(DOWNLOADS_DIR) if os.path.isfile(os.path.join(DOWNLOADS_DIR, f)) and f not in [d['path'] for d in downloaded_files_info]]
                        if len(potential_files) == 1:
                             actual_downloaded_path = potential_files[0]


                if actual_downloaded_path:
                    downloaded_files_info.append({'path': actual_downloaded_path, 'original_url': url, 'title': info.get('title', os.path.basename(actual_downloaded_path))})
                    app.logger.info(f"Successfully downloaded {actual_downloaded_path} for URL {url}")
                else:
                    # This path means error_code was not 0, or file wasn't found after download.
                    error_msg_detail = f"Download failed (error code: {error_code})"
                    if info and info.get('title'):
                        error_msg_detail += f" for video '{info.get('title')}'"
                    else:
                        error_msg_detail += f" for URL '{url}'"
                    if not os.path.exists(filename_from_info): # If even the prepared filename doesn't exist
                        error_msg_detail += ". Output file not found."

                    download_errors.append({'url': url, 'error': error_msg_detail})
                    app.logger.error(error_msg_detail)


        except yt_dlp.utils.DownloadError as e:
            # Extract a cleaner error message if possible
            # yt-dlp errors often have useful info directly in str(e)
            error_str = str(e).split('ERROR: ')[-1].strip()
            app.logger.error(f"DownloadError for {url}: {error_str}")
            download_errors.append({'url': url, 'error': error_str})
        except Exception as e:
            app.logger.error(f"Generic error for {url}: {str(e)}")
            download_errors.append({'url': url, 'error': f'An unexpected error occurred: {str(e)}'})

    if not downloaded_files_info:
        return jsonify({'status': 'error', 'errors': download_errors or [{'url': 'N/A', 'error': 'No videos were successfully downloaded and no specific errors captured.'}]}), 500

    try:
        response_headers = {}
        if download_errors:
            # Add errors as a JSON string in a custom header
            response_headers['X-Download-Errors'] = json.dumps(download_errors)

        if len(downloaded_files_info) == 1:
            file_path = downloaded_files_info[0]['path']
            app.logger.info(f"Sending single file: {file_path}")
            response = make_response(send_file(file_path, as_attachment=True))
            response.call_on_close(lambda: cleanup_files([file_path]))
        else:
            zip_filename = 'youtube_videos.zip'
            zip_path = os.path.join(DOWNLOADS_DIR, zip_filename)
            app.logger.info(f"Creating zip file: {zip_path}")
            with zipfile.ZipFile(zip_path, 'w') as zf:
                for file_info in downloaded_files_info:
                    zf.write(file_info['path'], os.path.basename(file_info['path']))
            
            response = make_response(send_file(zip_path, as_attachment=True, download_name=zip_filename))
            files_to_cleanup = [info['path'] for info in downloaded_files_info] + [zip_path]
            response.call_on_close(lambda: cleanup_files(files_to_cleanup))

        for key, value in response_headers.items():
            response.headers[key] = value
        
        # Add a header for successful files to help frontend
        response.headers['X-Successful-Downloads'] = json.dumps([{'original_url': f['original_url'], 'filename': os.path.basename(f['path']), 'title':f['title']} for f in downloaded_files_info])

        return response

    except Exception as e:
        app.logger.error(f"Error sending file(s): {e}")
        files_to_cleanup = [info['path'] for info in downloaded_files_info] + [os.path.join(DOWNLOADS_DIR, 'youtube_videos.zip')]
        cleanup_files(files_to_cleanup) # Cleanup if sending fails
        return jsonify({'status': 'error', 'errors': [{'url': 'N/A', 'error': f'Error packaging or sending file(s): {str(e)}'}]}), 500


def cleanup_files(files_to_delete):
    """Deletes specified files."""
    app.logger.info(f"Attempting to clean up: {files_to_delete}")
    for f_path in files_to_delete:
        try:
            if os.path.exists(f_path):
                if os.path.isdir(f_path):
                    shutil.rmtree(f_path)
                    app.logger.info(f"Cleaned up directory: {f_path}")
                else:
                    os.remove(f_path)
                    app.logger.info(f"Cleaned up file: {f_path}")
        except Exception as e:
            app.logger.error(f"Error cleaning up file {f_path}: {e}")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)
