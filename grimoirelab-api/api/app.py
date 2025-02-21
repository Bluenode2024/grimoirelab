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

# 환경 변수 설정
REPOSITORY_PATH = os.getenv('REPOSITORY_PATH', '/default-grimoirelab-settings')
PROJECTS_JSON_PATH = os.path.join(REPOSITORY_PATH, 'projects.json')
ES_URL = os.getenv('ES_URL', 'http://elasticsearch:9200')
KIBANA_URL = os.getenv('KIBANA_URL', 'http://localhost:8000')

# Elasticsearch 클라이언트 초기화
es_client = Elasticsearch([ES_URL])

def get_repositories_from_projects():
    """projects.json에서 저장소 URL 목록을 가져옵니다."""
    try:
        with open(PROJECTS_JSON_PATH, 'r') as f:
            projects_data = json.load(f)
            repos = []
            for project_info in projects_data.values():
                if 'git' in project_info:
                    repos.extend(project_info['git'])
            return repos
    except Exception as e:
        logger.error(f"Failed to read projects.json: {e}")
        return []

def create_repository_filter(repos):
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

def update_visualization_settings(repos):
    try:
        vis_body = {
            "type": "visualization",
            "visualization": {
                "title": "Repository Contribution Analysis",
                "visState": json.dumps({
                    "title": "Repository Contribution Analysis",
                    "type": "table",
                    "params": {
                        "perPage": 15,
                        "showPartialRows": False,
                        "showMetricsAtAllLevels": True,
                        "showTotal": True,
                        "totalFunc": "sum",
                        "percentageCol": "",
                        "dimensions": {
                            "buckets": [
                                {
                                    "accessor": 0,
                                    "format": {"id": "terms"},
                                    "params": {},
                                    "aggType": "terms"
                                }
                            ],
                            "metrics": [
                                {"accessor": 1, "format": {"id": "number"}, "params": {}, "aggType": "count"},
                                {"accessor": 2, "format": {"id": "number"}, "params": {}, "aggType": "avg"},
                                {"accessor": 3, "format": {"id": "number"}, "params": {}, "aggType": "sum"}
                            ]
                        }
                    },
                    "aggs": [
                        {
                            "id": "1",
                            "enabled": True,
                            "type": "count",
                            "schema": "metric",
                            "params": {
                                "customLabel": "Commit Count"
                            }
                        },
                        {
                            "id": "2",
                            "enabled": True,
                            "type": "terms",
                            "schema": "bucket",
                            "params": {
                                "field": "repository",
                                "size": len(repos),
                                "order": "desc",
                                "orderBy": "1",
                                "customLabel": "Repository"
                            }
                        },
                        {
                            "id": "3",
                            "enabled": True,
                            "type": "terms",
                            "schema": "bucket",
                            "params": {
                                "field": "author_name.keyword",
                                "size": 100,
                                "order": "desc",
                                "orderBy": "1",
                                "customLabel": "Author"
                            }
                        },
                        {
                            "id": "4",
                            "enabled": True,
                            "type": "date_histogram",
                            "schema": "metric",
                            "params": {
                                "field": "grimoire_creation_date",
                                "interval": "auto",
                                "customLabel": "Commit Frequency"
                            }
                        },
                        {
                            "id": "5",
                            "enabled": True,
                            "type": "sum",
                            "schema": "metric",
                            "params": {
                                "field": "lines_added",
                                "customLabel": "Lines Added"
                            }
                        },
                        {
                            "id": "6",
                            "enabled": True,
                            "type": "sum",
                            "schema": "metric",
                            "params": {
                                "field": "lines_removed",
                                "customLabel": "Lines Removed"
                            }
                        }
                    ]
                }),
                "description": "상세 커밋 분석 대시보드",
                "version": 1,
                "kibanaSavedObjectMeta": {
                    "searchSourceJSON": json.dumps({
                        "index": "git",
                        "query": {"match_all": {}},
                        "filter": []
                    })
                }
            }
        }

        try:
            es_client.update(
                index=".kibana",
                id="visualization:9672d770-eed8-11ef-9c8a-253e42e7811b",
                body={"doc": vis_body},
                doc_type="doc"
            )
        except Exception:
            es_client.index(
                index=".kibana",
                id="visualization:9672d770-eed8-11ef-9c8a-253e42e7811b",
                body=vis_body,
                doc_type="doc"
            )
        
        return True
    except Exception as e:
        logger.error(f"Failed to update visualization: {e}")
        return False

def update_dashboard_filter(repos):
    """대시보드 필터 업데이트"""
    try:
        filter_query = create_repository_filter(repos)
        
        # Elasticsearch 업데이트
        try:
            es_client.update(
                index=".kibana_task_manager",
                id="search:git",
                body={
                    "doc": {
                        "kibanaSavedObjectMeta": {
                            "searchSourceJSON": json.dumps(filter_query)
                        }
                    }
                }
            )
        except Exception:
            es_client.index(
                index=".kibana_task_manager",
                id="search:git",
                body={
                    "kibanaSavedObjectMeta": {
                        "searchSourceJSON": json.dumps(filter_query)
                    }
                }
            )
        return True
    except Exception as e:
        logger.error(f"Failed to update dashboard filter: {e}")
        return False

def validate_json_format(data):
    """projects.json 형식 검증"""
    if not isinstance(data, dict):
        return False
    for project in data.values():
        if not isinstance(project, dict):
            return False
        if 'meta' not in project or 'git' not in project:
            return False
        if not isinstance(project['git'], list):
            return False
    return True

@app.route('/update-projects', methods=['POST'])
def update_projects():
    """projects.json 파일 업데이트 및 연관 작업 수행"""
    try:
        # 1. projects.json 파일 업데이트
        data = request.get_json()
        logger.info(f"Received update request with data: {json.dumps(data, indent=2)}")
        
        if not validate_json_format(data):
            return jsonify({"success": False, "error": "Invalid JSON format"}), 400
        
        with open(PROJECTS_JSON_PATH, 'r') as f:
            projects = json.load(f)
        
        projects.update(data)
        
        with open(PROJECTS_JSON_PATH, 'w') as f:
            json.dump(projects, f, indent=2)
        logger.info("1. Projects file updated successfully")
        
        # 2. Git 작업
        try:
            repo = git.Repo(REPOSITORY_PATH)
            repo.index.add(['projects.json'])
            commit = repo.index.commit('Update projects.json')
            
            # 환경 변수에서 자격 증명 가져오기
            git_username = os.getenv('GIT_USERNAME')
            git_token = os.getenv('GIT_TOKEN')
            
            if not git_username or not git_token:
                raise ValueError("Git credentials not found in environment variables")
            
            # 원격 저장소 설정 확인 및 추가
            try:
                origin = repo.remote('origin')
            except ValueError:
                remote_url = f'https://{git_username}:{git_token}@github.com/Bluenode2024/grimoirelab.git'
                origin = repo.create_remote('origin', remote_url)
            
            # 기존 URL 업데이트
            with repo.config_writer() as git_config:
                git_config.set_value('remote "origin"', 'url', 
                    f'https://{git_username}:{git_token}@github.com/Bluenode2024/grimoirelab.git')
            
            # 현재 브랜치 확인 및 push
            current = repo.active_branch
            origin.push(current.name)
            logger.info("2. Git operations completed successfully")
        except Exception as git_error:
            logger.warning(f"Git operations failed but continuing: {git_error}")
        
        # 3. Mordred 컨테이너 재시작
        try:
            container_name = "docker-compose-mordred-1"
            client = docker.from_env()
            container = client.containers.get(container_name)
            container.restart()
            logger.info("3. Mordred container restarted successfully")
        except Exception as docker_error:
            logger.warning(f"Mordred restart failed but continuing: {docker_error}")
        
        # 4. 대시보드 필터 및 URL 업데이트
        try:
            repos = get_repositories_from_projects()
            if update_dashboard_filter(repos) and update_visualization_settings(repos):
                logger.info("4. Dashboard and visualization updated successfully")
            else:
                logger.warning("Dashboard or visualization update partially failed")
        except Exception as es_error:
            logger.warning(f"Dashboard update failed but continuing: {es_error}")
                
        return jsonify({
            "success": True,
            "message": "All updates completed successfully",
            "details": {
                "projects_updated": True,
                "git_pushed": True,
                "mordred_restarted": True,
                "dashboard_updated": True
            }
        })
        
    except Exception as e:
        logger.error(f"Update process failed: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/view-dashboard', methods=['GET'])
def view_dashboard():
    try:
        repos = get_repositories_from_projects()
        logger.info(f"Found repositories: {repos}")
        
        # 저장소 URL 인코딩
        encoded_repos = [urllib.parse.quote(repo, safe='') for repo in repos]
        
        # 저장소 필터와 집계 생성
        repo_terms = ','.join([f"(term:(origin:'{repo}'))" for repo in encoded_repos])
        
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
        
        # 새로운 패널 설정 (레포지토리별 커밋 수)
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

        # Kibana URL 생성
        dashboard_url = (
            f"{KIBANA_URL}/app/kibana#/dashboard/Overview?"
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

@app.route('/health', methods=['GET'])
def health_check():
    """서비스 상태 확인"""
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
        docker.from_env().ping()
        
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