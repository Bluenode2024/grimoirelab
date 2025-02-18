from flask import Flask

def create_app():
    app = Flask(__name__)
    
    from app.routes.repository import repo_blueprint
    app.register_blueprint(repo_blueprint)
    
    @app.route('/')
    def health_check():
        return {"status": "healthy"}
    
    return app