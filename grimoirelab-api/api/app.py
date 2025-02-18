# grimoirelab/api/app.py
from flask import Flask, request, jsonify
import json
import os
import docker
from threading import Thread
import logging
import git
from datetime import datetime

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# default-grimoirelab-settings의 projects.json 경로 설정
PROJECTS_JSON_PATH = os.getenv('PROJECTS_JSON_PATH', 
    '/default-grimoirelab-settings/projects.json')

class GrimoireManager:
    def __init__(self):
        try:
            self.client = docker.from_env()
            self.current_service = 'blue'
            # projects.json 경로 확인
            if not os.path.exists(PROJECTS_JSON_PATH):
                raise FileNotFoundError(f"Projects file not found at {PROJECTS_JSON_PATH}")
            logger.info(f"GrimoireManager initialized with projects file at {PROJECTS_JSON_PATH}")
            
            # Git 저장소 경로를 projects.json이 있는 디렉토리로 설정
            self.repo_path = os.path.dirname(PROJECTS_JSON_PATH)
            logger.info(f"Using repository path: {self.repo_path}")
            
            try:
                self.repo = git.Repo(self.repo_path)
                logger.info("Git repository found")
            except git.InvalidGitRepositoryError:
                logger.info(f"Initializing new Git repository at {self.repo_path}")
                self.repo = git.Repo.init(self.repo_path)
                
                # Git 초기 설정
                self.repo.config_writer().set_value("user", "name", "jaerius").release()
                self.repo.config_writer().set_value("user", "email", "rylynn1029@naver.com").release()
                
                # GitHub 원격 저장소 설정
                token = os.getenv('GIT_TOKEN')
                username = os.getenv('GIT_USERNAME')
                if not token or not username:
                    raise ValueError("GIT_TOKEN and GIT_USERNAME environment variables are required")
                
                try:
                    authenticated_url = f"https://{username}:{token}@github.com/{username}/grimoirelab-1.git"
                    origin = self.repo.create_remote('origin', authenticated_url)
                    logger.info("Remote 'origin' added with authentication")
                except git.GitCommandError:
                    logger.info("Remote 'origin' already exists")
                
                # 초기 커밋 및 푸시
                self.repo.index.add([PROJECTS_JSON_PATH])
                commit = self.repo.index.commit("Initial commit")
                logger.info(f"Created initial commit: {commit.hexsha}")
                
                # 초기 푸시 (--set-upstream)
                try:
                    self.repo.git.push('--set-upstream', 'origin', 'master')
                    logger.info("Initial push successful")
                except git.GitCommandError as e:
                    logger.error(f"Failed to push: {str(e)}")
                    raise
                
        except Exception as e:
            logger.error(f"Failed to initialize: {str(e)}")
            raise

    def validate_json_format(self, data):
        """projects.json 형식 검증"""
        required_fields = ['meta', 'git']
        if not isinstance(data, dict):
            return False
        for project in data.values():
            if not all(field in project for field in required_fields):
                return False
        return True

    def commit_and_push_changes(self, message):
        try:
            logger.info(f"Current repository path: {self.repo_path}")
            logger.info(f"Projects.json path: {PROJECTS_JSON_PATH}")
            
            # Git add
            self.repo.index.add([PROJECTS_JSON_PATH])
            logger.info("Added file to git index")
            
            # Git commit
            commit_message = f"Update projects.json: {message}"
            commit = self.repo.index.commit(commit_message)
            logger.info(f"Created commit: {commit.hexsha}")
            
            # Git push with token
            token = os.getenv('GIT_TOKEN')
            username = os.getenv('GIT_USERNAME')
            if not token or not username:
                raise ValueError("GIT_TOKEN and GIT_USERNAME environment variables are required")
            
            # 현재 브랜치 이름 확인
            current_branch = self.repo.active_branch.name
            logger.info(f"Current branch: {current_branch}")
            
            # 인증된 URL로 직접 푸시
            authenticated_url = f"https://{username}:{token}@github.com/{username}/grimoirelab-1.git"
            self.repo.remote('origin').set_url(authenticated_url)
            logger.info(f"Pushing to: https://{username}:****@github.com/{username}/grimoirelab-1.git")
            
            # 강제 푸시 (필요한 경우)
            push_info = self.repo.git.push('origin', current_branch)
            logger.info(f"Push result: {push_info}")
            
            return True
        except Exception as e:
            logger.error(f"Failed to commit and push changes: {str(e)}")
            logger.exception("Detailed error:")
            return False

    def update_projects(self, new_data):
        try:
            # 형식 검증
            if not self.validate_json_format(new_data):
                logger.error("Invalid project data format")
                return False, "Invalid data format"

            # 현재 projects.json 읽기
            with open(PROJECTS_JSON_PATH, 'r') as f:
                current_projects = json.load(f)
            
            # 새 레포지토리 추가
            current_projects.update(new_data)
            
            # 임시 파일에 먼저 저장 (같은 디렉토리에)
            temp_path = os.path.join(os.path.dirname(PROJECTS_JSON_PATH), 
                'projects.json.temp')
            
            with open(temp_path, 'w') as f:
                json.dump(current_projects, f, indent=2)
            
            # 성공적으로 저장되면 원본 파일 교체
            os.replace(temp_path, PROJECTS_JSON_PATH)
            logger.info("Projects file updated successfully")
            
            # 파일 업데이트 후 Git 커밋 및 푸시
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            project_names = ", ".join(new_data.keys())
            self.commit_and_push_changes(f"{timestamp} - Added/Updated projects: {project_names}")
            
            # 비동기로 서비스 재시작
            Thread(target=self.restart_service).start()
            
            return True, "Update successful"
            
        except Exception as e:
            logger.error(f"Update failed: {e}")
            return False, str(e)

    def restart_service(self):
        try:
            # Mordred 컨테이너 찾기
            containers = self.client.containers.list(
                filters={'name': 'docker-compose-mordred-1'}  # 컨테이너 이름 수정
            )
            
            if not containers:
                logger.error("No Mordred container found")
                return
                
            container = containers[0]
            logger.info(f"Restarting container: {container.name}")
            container.restart()
            logger.info("Container restart completed")
            
        except Exception as e:
            logger.error(f"Restart failed: {e}")

manager = GrimoireManager()

@app.route('/update-projects', methods=['POST'])
def update_projects():
    try:
        new_repo_data = request.json
        logger.info(f"Received update request with data: {json.dumps(new_repo_data, indent=2)}")
        
        if not new_repo_data:
            return jsonify({"error": "No data provided"}), 400
            
        success, message = manager.update_projects(new_repo_data)
        logger.info(f"Update result: success={success}, message={message}")
        
        if success:
            return jsonify({
                "message": "Projects updated successfully",
                "path": PROJECTS_JSON_PATH,
                "updated_data": new_repo_data
            })
        else:
            return jsonify({"error": message}), 500
            
    except Exception as e:
        logger.error(f"Update projects endpoint error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    try:
        # projects.json 존재 및 읽기 가능 확인
        if not os.path.exists(PROJECTS_JSON_PATH):
            return jsonify({
                "status": "unhealthy", 
                "error": f"Projects file not found at {PROJECTS_JSON_PATH}"
            }), 500
            
        with open(PROJECTS_JSON_PATH, 'r') as f:
            json.load(f)
            
        # Docker 연결 확인
        manager.client.ping()
        
        return jsonify({
            "status": "healthy",
            "projects_file": PROJECTS_JSON_PATH,
            "docker": "connected"
        })
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({
            "status": "unhealthy",
            "error": str(e)
        }), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=9000)