from app import create_app

try:
    from werkzeug.urls import quote as url_quote
except ImportError:
    from werkzeug.urls import url_quote

app = create_app()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000) 