# grimoirelab/api/app.py
from flask import Flask, request, jsonify
import json
import os
import docker
from threading import Thread
import logging
import git
from datetime import datetime
from elasticsearch import Elasticsearch
from flask import redirect, url_for
import urllib.parse

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    from werkzeug.urls import quote as url_quote
except ImportError:
    from werkzeug.urls import url_quote

app = Flask(__name__)

# Elasticsearch 클라이언트 초기화
es_client = Elasticsearch(['http://elasticsearch:9200'])

# default-grimoirelab-settings의 projects.json 경로 설정
PROJECTS_JSON_PATH = os.getenv('PROJECTS_JSON_PATH', 
    '/default-grimoirelab-settings/projects.json')
base_url = os.getenv('KIBANA_URL', 'http://localhost:8000')

def get_repositories_from_projects():
    """projects.json에서 저장소 URL 목록을 가져옵니다."""
    try:
        with open(PROJECTS_JSON_PATH, 'r') as f:
            projects_data = json.load(f)
            repos = []
            for project_name, project_info in projects_data.items():
                if 'git' in project_info:
                    repos.extend(project_info['git'])
            return repos
    except Exception as e:
        logger.error(f"Failed to read projects.json: {e}")
        return []

def create_repository_filter(repos):
    """저장소 목록으로 Elasticsearch 쿼리를 생성합니다."""
    """저장소 목록으로 Elasticsearch 쿼리를 생성합니다."""
    should_clauses = [
        {"term": {"origin": repo}} for repo in repos
    ]
    
    return {
        "size": 0,
        "query": {
            "bool": {
                "should": should_clauses
            }
        },
        "aggs": {
            "by_repository": {
                "terms": {
                    "field": "origin",
                    "size": 10
                },
                "aggs": {
                    "by_authors": {
                        "terms": {
                            "field": "author_name",
                            "size": 100
                        },
                        "aggs": {
                            "commit_count": {
                                "value_count": {
                                    "field": "hash"
                                }
                            }
                        }
                    }
                }
            }
        }
    }

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

    def update_projects(self, new_data):
        """projects.json 파일 업데이트 및 Git 커밋/푸시"""
        try:
            if not self.validate_json_format(new_data):
                return False, "Invalid JSON format"
                
            # projects.json 파일 업데이트
            with open(PROJECTS_JSON_PATH, 'w') as f:
                json.dump(new_data, f, indent=4)
            logger.info("Projects file updated successfully")
            
            try:
                # Git 커밋 및 푸시
                self.commit_and_push_changes("Update projects.json")
            except Exception as e:
                logger.error(f"Git operation failed: {e}")
                # Git 실패해도 계속 진행
            
            # Mordred 컨테이너 재시작
            self.restart_mordred()
            
            # 대시보드 필터 업데이트
            try:
                self.update_dashboard_filter()
            except Exception as e:
                logger.error(f"Failed to update dashboard filter: {e}")
            
            return True, "Update successful"
            
        except Exception as e:
            logger.error(f"Failed to update projects: {e}")
            return False, str(e)

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
            self.repo.git.push(authenticated_url, current_branch)
            logger.info("Changes pushed successfully")
            
        except Exception as e:
            logger.error(f"Failed to commit and push changes: {e}")
            raise

    def restart_mordred(self):
        """Mordred 컨테이너 재시작"""
        try:
            containers = self.client.containers.list(
                filters={'name': 'docker-compose-mordred-1'}
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

    def update_dashboard_filter(self):
        """대시보드 필터 업데이트"""
        try:
            repos = get_repositories_from_projects()
            filter_query = create_repository_filter(repos)
            
            # .kibana 인덱스의 검색 설정 업데이트
            es_client.update(
                index='.kibana_task_manager',  # 또는 현재 사용 중인 .kibana 인덱스
                id='search:git',
                body={
                    "doc": {
                        "kibanaSavedObjectMeta": {
                            "searchSourceJSON": json.dumps(filter_query)
                        }
                    }
                }
            )
            logger.info("Dashboard filter updated successfully")
            
        except Exception as e:
            logger.error(f"Failed to update dashboard filter: {e}")
            raise

manager = GrimoireManager()

# 라우트 등록
@app.route('/update-dashboard-filter', methods=['POST'])
def update_dashboard_filter():
    """대시보드 필터를 수동으로 업데이트합니다."""
    try:
        logger.info("Updating dashboard filter")
        repos = get_repositories_from_projects()
        filter_query = create_repository_filter(repos)
        
        # .kibana 인덱스의 검색 설정 업데이트
        es_client.update(
            index='.kibana_task_manager',
            id='search:git',
            body={
                "doc": {
                    "kibanaSavedObjectMeta": {
                        "searchSourceJSON": json.dumps(filter_query)
                    }
                }
            }
        )
        logger.info("Dashboard filter updated successfully")
        return jsonify({"message": "Dashboard filter updated successfully"})
    except Exception as e:
        logger.error(f"Failed to update dashboard filter: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/get-filtered-data', methods=['GET'])
def get_filtered_data():
    """현재 필터가 적용된 데이터를 가져옵니다."""
    try:
        repos = get_repositories_from_projects()
        filter_query = create_repository_filter(repos)
        
        # git 인덱스에서 필터링된 데이터 검색
        result = es_client.search(
            index='git*',
            body=filter_query
        )
        
        return jsonify(result['aggregations'])
    except Exception as e:
        logger.error(f"Failed to get filtered data: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/view-dashboard', methods=['GET'])
def view_dashboard():
    try:
        repos = get_repositories_from_projects()
        logger.info(f"Found repositories: {repos}")
        
        # 저장소 URL 인코딩
        encoded_repos = [urllib.parse.quote(repo, safe='') for repo in repos]
        
        # 저장소 필터와 집계 생성
        repo_terms = ','.join([f"(term:(origin:'{repo}'))" for repo in encoded_repos])
        
        # 시각화 설정 추가
        visualization_config = (
            "vis:(aggs:!("
            "(enabled:!t,id:'1',params:(field:hash),schema:metric,type:count),"
            "(enabled:!t,id:'2',params:(field:origin,missingBucket:!f,missingBucketLabel:Missing,"
            "order:desc,orderBy:'1',otherBucket:!f,otherBucketLabel:Other,size:10),"
            "schema:bucket,type:terms),"
            "(enabled:!t,id:'3',params:(field:author_name,missingBucket:!f,missingBucketLabel:Missing,"
            "order:desc,orderBy:'1',otherBucket:!f,otherBucketLabel:Other,size:20),"
            "schema:bucket,type:terms)),"
            "params:(perPage:10,showMetricsAtAllLevels:!f,showPartialRows:!f,showTotal:!f,"
            "sort:(columnIndex:!n,direction:!n),totalFunc:sum),"
            "title:'Repository%20Commits%20by%20Author',type:table)"
        )
        
        # 기본 필터
        base_filters = [
            "('$state':(store:appState),meta:(alias:'Empty%20Commits',disabled:!f,index:git,key:files,negate:!t,params:(query:'0',type:phrase),type:phrase,value:'0'),query:(match:(files:(query:'0',type:phrase))))",
            "('$state':(store:appState),meta:(alias:Bots,disabled:!f,index:git,key:author_bot,negate:!t,params:(query:!t,type:phrase),type:phrase,value:true),query:(match:(author_bot:(query:!t,type:phrase))))"
        ]
        
        # 저장소 필터
        repo_filter = (
            "('$state':(store:appState),"
            "meta:(alias:!n,disabled:!f,index:git,key:size,negate:!f,type:custom,value:'0'),"
            f"query:(bool:(should:!({repo_terms}))))"
        )
        
        # 모든 필터 결합
        all_filters = ','.join([*base_filters, repo_filter])
        
        # Kibana URL 생성
        base_url = os.getenv('KIBANA_URL', 'http://localhost:8000')
        # 새로운 패널 설정
        new_panel = (
            "(embeddableConfig:(title:'Commit Count by Repository',"
            "vis:(params:(config:(searchKeyword:''),sort:(columnIndex:!n,direction:!n)))),"
            "gridData:(h:20,i:'113',w:48,x:0,y:56),"
            "id:'9672d770-eed8-11ef-9c8a-253e42e7811b',"
            "panelIndex:'113',"
            "title:'Commit Count by Repository',"
            "type:visualization,"
            "version:'6.8.6')"
        )

        # panels 문자열에 새로운 패널 추가
        panels_str = (
            "panels:!((embeddableConfig:(title:Git),gridData:(h:8,i:'1',w:16,x:0,y:20),id:git_main_numbers,panelIndex:'1',title:Git,type:visualization,version:'6.8.6'),"
            "(embeddableConfig:(title:Commits,vis:(legendOpen:!f)),gridData:(h:8,i:'2',w:16,x:0,y:28),id:git_evolution_commits,panelIndex:'2',title:'Git%20Commits',type:visualization,version:'6.8.6'),"
            "(embeddableConfig:(title:Authors,vis:(legendOpen:!f)),gridData:(h:8,i:'3',w:16,x:0,y:36),id:git_evolution_authors,panelIndex:'3',title:'Git%20Authors',type:visualization,version:'6.8.6'),"
            "(embeddableConfig:(title:Organizations),gridData:(h:20,i:'5',w:16,x:16,y:0),id:git_commits_organizations,panelIndex:'5',title:Organizations,type:visualization,version:'6.8.6'),"
            "(embeddableConfig:(title:'Git%20Top%20Authors',vis:(params:(config:(searchKeyword:''),sort:(columnIndex:!n,direction:!n)))),gridData:(h:20,i:'111',w:16,x:0,y:0),id:git_overview_top_authors,panelIndex:'111',title:'Git%20Top%20Authors',type:visualization,version:'6.8.6'),"
            "(embeddableConfig:(title:'Git%20Top%20Projects',vis:(params:(config:(searchKeyword:''),sort:(columnIndex:!n,direction:!n)))),gridData:(h:20,i:'112',w:16,x:32,y:0),id:git_overview_top_projects,panelIndex:'112',title:'Git%20Top%20Projects',type:visualization,version:'6.8.6'),"
            f"{new_panel})"
        )

        # URL 생성 시 panels_str 사용
        dashboard_url = (
            f"{base_url}/app/kibana#/dashboard/Overview?"
            f"_g=(refreshInterval:(pause:!t,value:0),time:(from:now-5y,mode:quick,to:now))&"
            f"_a=(description:'Overview%20Panel%20by%20Bitergia',"
            f"filters:!({all_filters}),"
            f"{panels_str},"
            "fullScreenMode:!f,options:(darkTheme:!f,useMargins:!t),"
            "query:(language:lucene,query:'*'),timeRestore:!f,title:Overview,viewMode:view)"
        )
        
        logger.info(f"Redirecting to dashboard with URL: {dashboard_url}")
        return redirect(dashboard_url)
        
    except Exception as e:
        logger.error(f"Failed to redirect to dashboard: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

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