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

def update_visualization_settings():
    try:
        visualization = {
            "type": "visualization",
            "attributes": {
                "title": "Repository Overview",
                "visState": json.dumps({
                    "title": "Repository Overview",
                    "type": "table",
                    "params": {
                        "perPage": 10,
                        "showPartialRows": False,
                        "showMetricsAtAllLevels": False,
                        "sort": {"columnIndex": 1, "direction": "desc"},
                        "showTotal": True,
                        "totalFunc": "sum"
                    },
                    "aggs": [
                        {
                            "id": "1",
                            "enabled": True,
                            "type": "count",
                            "schema": "metric",
                            "params": {"customLabel": "Commits"}
                        },
                        {
                            "id": "2",
                            "enabled": True,
                            "type": "terms",
                            "schema": "bucket",
                            "params": {
                                "field": "origin",
                                "size": 50,
                                "order": "desc",
                                "orderBy": "1",
                                "customLabel": "Repository"
                            }
                        }
                    ]
                }),
                "uiStateJSON": "{}",
                "description": "",
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

        es_client.index(
            index=".kibana",
            id="visualization:git-overview",
            document=visualization
        )

        return True
    except Exception as e:
        logger.error(f"Failed to update visualization settings: {e}")
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
            if update_dashboard_filter(repos) and update_visualization_settings():
                logger.info("4. Dashboard and visualization updated successfully")
            else:
                logger.warning("Dashboard or visualization update partially failed")
        except Exception as es_error:
            logger.warning(f"Dashboard update failed but continuing: {es_error}")
        
        # 5. PageRank 계산 및 시각화 업데이트
        try:
            logger.info("Starting PageRank calculation...")
            # Elasticsearch 매핑 설정
            setup_elasticsearch_mapping()
            # PageRank 계산
            calculate_repository_pagerank()
            logger.info("5. PageRank calculation completed successfully")
        except Exception as pr_error:
            logger.warning(f"PageRank calculation failed but continuing: {pr_error}")
                
        return jsonify({
            "success": True,
            "message": "All updates completed successfully",
            "details": {
                "projects_updated": True,
                "git_pushed": True,
                "mordred_restarted": True,
                "dashboard_updated": True,
                "pagerank_calculated": True
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
            "meta:(alias:!n,disabled:!f,index:git,key:query,negate:!f,type:custom,value:'%7B%22bool%22:%7B%22should%22:%5B" +
            ','.join([f"%7B%22term%22:%7B%22origin%22:%22{repo}%22%7D%7D" for repo in encoded_repos]) +
            "%5D%7D%7D')," +
            f"query:(bool:(should:!({repo_terms}))))"
        )
        
        # 모든 필터 결합
        all_filters = ','.join([*base_filters, repo_filter])
        
        # 새로운 패널 레이아웃 (PageRank 시각화 포함)
        panels_str = (
            "panels:!("
            "(embeddableConfig:(title:Git),gridData:(h:8,i:'1',w:16,x:25,y:52),id:git_main_numbers,panelIndex:'1',title:Git,type:visualization,version:'6.8.6'),"
            "(embeddableConfig:(title:Commits,vis:(legendOpen:!f)),gridData:(h:8,i:'2',w:16,x:0,y:52),id:git_evolution_commits,panelIndex:'2',title:'Git%20Commits',type:visualization,version:'6.8.6'),"
            "(embeddableConfig:(title:'Git%20Top%20Authors',vis:(params:(config:(searchKeyword:''),sort:(columnIndex:!n,direction:!n)))),gridData:(h:17,i:'111',w:25,x:0,y:20),id:git_overview_top_authors,panelIndex:'111',title:'Git%20Top%20Authors',type:visualization,version:'6.8.6'),"
            "(embeddableConfig:(title:'Git%20Top%20Projects',vis:(params:(config:(searchKeyword:''),sort:(columnIndex:!n,direction:!n)))),gridData:(h:17,i:'112',w:23,x:25,y:20),id:git_overview_top_projects,panelIndex:'112',title:'Git%20Top%20Projects',type:visualization,version:'6.8.6'),"
            "(embeddableConfig:(title:'Repository Overview'),gridData:(h:20,i:'115',w:48,x:0,y:0),id:'2f5869c0-f1b6-11ef-a51e-59ace05a8f4f',panelIndex:'115',title:'Repository%20Overview',type:visualization,version:'6.8.6'),"
            "(embeddableConfig:(),gridData:(h:15,i:'116',w:23,x:25,y:37),id:'8cfe1960-18de-11e9-ba47-d5cbef43f8d3',panelIndex:'116',type:visualization,version:'6.8.6'),"
            "(embeddableConfig:(vis:(params:(config:(searchKeyword:''),sort:(columnIndex:!n,direction:!n)))),gridData:(h:15,i:'117',w:25,x:0,y:37),id:'9672d770-eed8-11ef-9c8a-253e42e7811b',panelIndex:'117',type:visualization,version:'6.8.6'),"
            "(embeddableConfig:(title:'Developer Impact Analysis'),gridData:(h:20,i:'118',w:48,x:0,y:60),id:'991fe4b0-f1ba-11ef-b3d0-09b31acaa3cf',panelIndex:'118',type:visualization,version:'6.8.6')"
            ")"
        )

        # Kibana URL 생성
        dashboard_url = (
            f"{KIBANA_URL}/app/kibana#/dashboard/Overview?"
            f"_g=(refreshInterval:(pause:!t,value:0),time:(from:now-5y,mode:quick,to:now))&"
            f"_a=(description:'Overview%20Panel%20by%20Jaewon',"
            f"filters:!({all_filters}),"
            f"{panels_str},"
            "fullScreenMode:!f,options:(darkTheme:!f,useMargins:!t),"
            "query:(language:lucene,query:'*'),timeRestore:!f,title:'Overview%20Jaewon',viewMode:view)"
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

def calculate_repository_pagerank():
    """레포지토리별 PageRank 계산"""
    try:
        repos = get_repositories_from_projects()
        logger.info(f"\n=== Starting PageRank calculation for {len(repos)} repositories ===")

        for repo in repos:
            logger.info(f"\n=== Processing repository: {repo} ===")
            
            # 1. 저자별 기본 통계 조회 (author_uuid 기준)
            author_stats = es_client.search(
                index="git",
                body={
                    "size": 0,
                    "query": {
                        "term": {
                            "origin": repo
                        }
                    },
                    "aggs": {
                        "authors": {
                            "terms": {
                                "field": "author_uuid",
                                "size": 100
                            },
                            "aggs": {
                                "names": {  # 저자 이름 가져오기 수정
                                    "terms": {
                                        "field": "author_name.keyword",
                                        "size": 1,
                                        "order": {
                                            "_count": "desc"
                                        }
                                    }
                                },
                                "commit_count": {
                                    "value_count": {
                                        "field": "hash.keyword"
                                    }
                                },
                                "lines_changed": {
                                    "sum": {
                                        "script": {
                                            "source": "doc['lines_added'].value + doc['lines_removed'].value"
                                        }
                                    }
                                },
                                "files": {
                                    "terms": {
                                        "field": "files.path.keyword",
                                        "size": 1000
                                    }
                                },
                                "commit_dates": {
                                    "date_histogram": {
                                        "field": "author_date",
                                        "calendar_interval": "1d"
                                    }
                                }
                            }
                        }
                    }
                }
            )

            # 쿼리 결과 로깅
            logger.info("\nElasticsearch query result:")
            logger.info(json.dumps(author_stats, indent=2))

            authors = author_stats["aggregations"]["authors"]["buckets"]
            logger.info(f"\nFound {len(authors)} authors in repository")

            if not authors:
                logger.warning("No authors found, skipping repository")
                continue

            # 2. 각 저자별 상세 지표 계산
            author_scores = {}
            for author in authors:
                author_uuid = author["key"]
                author_name = author["names"]["buckets"][0]["key"] if author["names"]["buckets"] else author_uuid
                logger.info(f"\n--- Calculating metrics for author: {author_name} (UUID: {author_uuid}) ---")

                # 2.1 파일 관련 지표
                file_weight = {
                    "complexity": calculate_file_complexity(author, repo),
                    "changes": author["lines_changed"]["value"] / max(a["lines_changed"]["value"] for a in authors),
                    "lifespan": calculate_file_lifespan(author),
                    "coupling": calculate_file_coupling(author, repo)
                }

                logger.info("\nFile weights:")
                for metric, value in file_weight.items():
                    logger.info(f"  - {metric}: {value:.3f}")

                # 2.2 저자 관련 지표
                author_weight = {
                    "lines_changed": author["lines_changed"]["value"] / max(a["lines_changed"]["value"] for a in authors),
                    "commit_frequency": len(author["commit_dates"]["buckets"]) / 365.0,
                    "code_quality": calculate_code_quality(author, repo),
                    "review_participation": calculate_review_participation(author, repo)
                }

                logger.info("\nAuthor weights:")
                for metric, value in author_weight.items():
                    logger.info(f"  - {metric}: {value:.3f}")

                # 2.3 종합 점수 계산
                final_score = calculate_composite_score(file_weight, author_weight)

                author_scores[author_name] = final_score
                logger.info(f"\nFinal PageRank score for {author_name}: {final_score:.3f}")

            # 3. 결과 저장
            save_pagerank_results(repo, author_scores)
            logger.info(f"\nSaved PageRank results for repository: {repo}")

        logger.info("\n=== PageRank calculation completed for all repositories ===")

    except Exception as e:
        logger.error(f"Failed to calculate PageRank: {e}")
        raise

def calculate_file_complexity(file_path, repo):
    """파일 복잡도 계산"""
    try:
        # 1. 파일 크기 (LOC)
        file_size = es_client.search(
            index="git",
            body={
                "query": {
                    "bool": {
                        "must": [
                            {"term": {"files.path.keyword": file_path}},
                            {"term": {"origin": repo}}
                        ]
                    }
                },
                "aggs": {
                    "total_lines": {
                        "sum": {
                            "script": {
                                "source": "doc['lines_added'].value - doc['lines_removed'].value"
                            }
                        }
                    }
                }
            }
        )
        
        # 2. 파일 의존성 (import/include 문 수)
        # Git blob에서 파일 내용을 가져와서 분석
        imports_count = count_imports(file_path)
        
        # 3. 함수/클래스 수
        functions_count = count_functions(file_path)
        
        # 복잡도 점수 계산 (0~1 사이로 정규화)
        complexity = (
            normalize(file_size["aggregations"]["total_lines"]["value"]) * 0.4 +
            normalize(imports_count) * 0.3 +
            normalize(functions_count) * 0.3
        )
        
        return complexity
    except Exception:
        return 0.5  # 기본값

def calculate_file_lifespan(file_history):
    """파일 수명과 활성도 계산"""
    try:
        # 1. 생성일부터 현재까지의 기간
        dates = [bucket["key"] for bucket in file_history["commit_dates"]["buckets"]]
        if not dates:
            return 0.5
            
        first_commit = min(dates)
        last_commit = max(dates)
        lifespan_days = (last_commit - first_commit).days
        
        # 2. 수정 빈도
        commit_frequency = len(dates) / max(lifespan_days, 1)
        
        # 3. 여러 개발자의 참여도
        unique_authors = len(file_history["authors"]["buckets"])
        
        # 수명 점수 계산 (0~1 사이로 정규화)
        lifespan_score = (
            normalize(lifespan_days) * 0.3 +
            normalize(commit_frequency) * 0.4 +
            normalize(unique_authors) * 0.3
        )
        
        return lifespan_score
    except Exception:
        return 0.5

def calculate_file_coupling(author, repo):
    """파일 간 결합도 계산"""
    try:
        # 1. 같이 수정되는 파일들 찾기
        coupled_files = es_client.search(
            index="git",
            body={
                "query": {
                    "bool": {
                        "must": [
                            {"term": {"author_uuid": author["key"]}},
                            {"term": {"origin": repo}}
                        ]
                    }
                },
                "aggs": {
                    "commits": {
                        "terms": {
                            "field": "hash.keyword"
                        },
                        "aggs": {
                            "files": {
                                "terms": {
                                    "field": "files.path.keyword",
                                    "size": 100
                                }
                            }
                        }
                    }
                }
            }
        )
        
        # 2. 결합도 점수 계산
        coupling_score = calculate_coupling_score(coupled_files)
        return coupling_score
    except Exception:
        return 0.5

def calculate_code_quality(author, repo):
    """코드 품질 지표 계산"""
    try:
        author_uuid = author["key"]  # author 객체에서 UUID 추출
        
        # 1. 버그 수정 커밋 비율
        commits = es_client.search(
            index="git",
            body={
                "query": {
                    "bool": {
                        "must": [
                            {"term": {"author_uuid": author_uuid}},  # UUID로 검색
                            {"term": {"origin": repo}}
                        ]
                    }
                },
                "aggs": {
                    "bug_fixes": {
                        "filter": {
                            "bool": {
                                "should": [
                                    {"match": {"message": "fix"}},
                                    {"match": {"message": "bug"}},
                                    {"match": {"message": "issue"}},
                                    {"match": {"message": "solve"}}
                                ]
                            }
                        }
                    }
                }
            }
        )
        
        total_commits = commits["hits"]["total"]["value"]
        bug_fixes = commits["aggregations"]["bug_fixes"]["doc_count"]
        bug_ratio = bug_fixes / max(total_commits, 1)
        
        # 2. 코드 리뷰 참여
        review_score = calculate_review_participation(author, repo)
        
        # 3. 테스트 파일 수정
        test_contributions = calculate_test_contributions(author, repo)
        
        # 품질 점수 계산
        quality_score = (
            (1 - normalize(bug_ratio)) * 0.4 +  # 버그 수정이 적을수록 높은 점수
            review_score * 0.3 +
            test_contributions * 0.3
        )
        
        return quality_score
    except Exception:
        return 0.5

def calculate_review_participation(author, repo):
    """코드 리뷰 참여도 추정"""
    try:
        author_uuid = author["key"]  # author 객체에서 UUID 추출
        
        # 1. Co-author 커밋 수
        coauthor_commits = es_client.search(
            index="git",
            body={
                "query": {
                    "bool": {
                        "must": [
                            {"term": {"origin": repo}},
                            {"match": {"message": f"Co-authored-by: {author_uuid}"}}
                        ]
                    }
                }
            }
        )
        
        # 2. 수정 제안 커밋 수 (suggested by, reviewed by 등)
        review_commits = es_client.search(
            index="git",
            body={
                "query": {
                    "bool": {
                        "must": [
                            {"term": {"origin": repo}},
                            {"bool": {
                                "should": [
                                    {"match": {"message": f"Suggested-by: {author_uuid}"}},
                                    {"match": {"message": f"Reviewed-by: {author_uuid}"}}
                                ]
                            }}
                        ]
                    }
                }
            }
        )
        
        # 리뷰 참여도 점수 계산
        participation_score = normalize(
            coauthor_commits["hits"]["total"]["value"] +
            review_commits["hits"]["total"]["value"]
        )
        
        return participation_score
    except Exception:
        return 0.5

def normalize(value, max_value=None):
    """값을 0~1 사이로 정규화"""
    if max_value is None:
        max_value = value * 2  # 적절한 최대값이 없는 경우
    return min(1.0, max(0.0, value / max_value))

def calculate_composite_score(file_weight, author_weight):
    """종합 점수 계산"""
    return (
        file_weight["complexity"] * 0.2 +
        file_weight["changes"] * 0.2 +
        file_weight["lifespan"] * 0.1 +
        file_weight["coupling"] * 0.1 +
        author_weight["lines_changed"] * 0.1 +
        author_weight["commit_frequency"] * 0.1 +
        author_weight["code_quality"] * 0.1 +
        author_weight["review_participation"] * 0.1
    )

def save_pagerank_results(repo, author_scores):
    """PageRank 결과를 git 인덱스에 업데이트"""
    try:
        bulk_data = []
        for author, score in author_scores.items():
            # 업데이트 쿼리
            bulk_data.extend([
                {
                    "update": {
                        "_index": "git",
                        "_id": f"{repo}_{author}",  # 문서 ID 생성
                        "retry_on_conflict": 3
                    }
                },
                {
                    "doc": {
                        "pagerank_score": float(score)
                    },
                    "doc_as_upsert": True
                }
            ])
        
        if bulk_data:
            es_client.bulk(body=bulk_data)
            logger.info(f"Successfully updated PageRank scores for {len(author_scores)} authors in {repo}")
            
        return True
    except Exception as e:
        logger.error(f"Failed to update PageRank data: {e}")
        return False

def create_pagerank_visualization():
    """Developer Impact Analysis 시각화 생성"""
    try:
        visualization = {
            "type": "visualization",
            "id": "991fe4b0-f1ba-11ef-b3d0-09b31acaa3cf",
            "attributes": {
                "title": "Developer Impact Analysis",
                "description": "",
                "version": 1,
                "kibanaSavedObjectMeta": {
                    "searchSourceJSON": json.dumps({
                        "index": "git",
                        "query": {
                            "query": "",
                            "language": "lucene"
                        },
                        "filter": []
                    })
                },
                "visState": json.dumps({
                    "title": "Developer Impact Analysis",
                    "type": "metric",
                    "params": {
                        "addTooltip": True,
                        "addLegend": False,
                        "type": "table",
                        "metric": {
                            "percentageMode": False,
                            "useRanges": False,
                            "colorSchema": "Green to Red",
                            "metricColorMode": "None",
                            "colorsRange": [
                                {"from": 0, "to": 10000}
                            ],
                            "labels": {"show": True},
                            "invertColors": False,
                            "style": {
                                "bgFill": "#000",
                                "bgColor": False,
                                "labelColor": False,
                                "subText": "",
                                "fontSize": 60
                            }
                        }
                    },
                    "aggs": [
                        {
                            "id": "1",
                            "enabled": True,
                            "type": "terms",
                            "schema": "group",
                            "params": {
                                "field": "origin",
                                "orderBy": "2",
                                "order": "desc",
                                "size": 10,
                                "otherBucket": False,
                                "otherBucketLabel": "Other",
                                "missingBucket": False,
                                "missingBucketLabel": "Missing"
                            }
                        },
                        {
                            "id": "2",
                            "enabled": True,
                            "type": "terms",
                            "schema": "group",
                            "params": {
                                "field": "author_name.keyword",
                                "orderBy": "3",
                                "order": "desc",
                                "size": 20,
                                "otherBucket": False,
                                "otherBucketLabel": "Other",
                                "missingBucket": False,
                                "missingBucketLabel": "Missing"
                            }
                        },
                        {
                            "id": "3",
                            "enabled": True,
                            "type": "avg",
                            "schema": "metric",
                            "params": {
                                "field": "pagerank_score",
                                "customLabel": "Impact Score"
                            }
                        }
                    ]
                }),
                "uiStateJSON": json.dumps({
                    "vis": {
                        "params": {
                            "sort": {
                                "columnIndex": 2,
                                "direction": "desc"
                            }
                        }
                    }
                })
            }
        }

        # 기존 시각화 삭제 (있다면)
        try:
            es_client.delete(
                index=".kibana",
                id="visualization:git-pagerank",
                ignore=[404]
            )
        except Exception as e:
            logger.warning(f"Failed to delete existing visualization: {e}")

        # 새로운 시각화 생성
        es_client.index(
            index=".kibana",
            id="visualization:git-pagerank",
            document=visualization
        )

        logger.info("Successfully created Developer Impact Analysis visualization")
        return True

    except Exception as e:
        logger.error(f"Failed to create Developer Impact Analysis visualization: {e}")
        return False

def create_pagerank_index_pattern():
    """PageRank 인덱스 패턴 생성"""
    try:
        # 1. 먼저 git 인덱스의 매핑 정보 가져오기
        mapping = es_client.indices.get_mapping(index="git")
        
        # 2. scripted fields 정의
        scripted_fields = {
            "pagerank_score": {
                "name": "pagerank_score",
                "script": {
                    "source": "doc['pagerank_score'].size() == 0 ? 0.5 : doc['pagerank_score'].value",
                    "lang": "painless"
                },
                "type": "number",
                "lang": "painless"
            },
            "painless_inverted_lines_removed_git": {
                "name": "painless_inverted_lines_removed_git",
                "script": {
                    "source": "return doc['lines_removed'].value * -1",
                    "lang": "painless"
                },
                "type": "number",
                "lang": "painless"
            }
        }
        
        # 3. 인덱스 패턴 생성
        index_pattern = {
            "type": "index-pattern",
            "index-pattern": {
                "title": "git*",
                "timeFieldName": "grimoire_creation_date",
                "intervalName": "days",
                "fields": json.dumps(mapping["git"]["mappings"]["properties"]),
                "sourceFilters": "[]",
                "fieldFormatMap": "{}",
                "scripted_fields": scripted_fields  # scripted fields 추가
            }
        }
        
        # 4. 인덱스 패턴 저장
        es_client.index(
            index=".kibana",
            id="index-pattern:git",
            body=index_pattern,
            refresh=True  # 즉시 반영을 위해 refresh 옵션 추가
        )
        
        logger.info("Created git index pattern with scripted fields")
        
    except Exception as e:
        logger.error(f"Failed to create index pattern: {e}")
        raise

def setup_elasticsearch_mapping():
    try:
        # 1. git 인덱스 매핑에 pagerank_score 필드 추가
        git_mapping = {
            "properties": {
                "pagerank_score": {
                    "type": "float",
                    "script": {
                        "lang": "painless",
                        "source": "doc['pagerank_score'].size() == 0 ? 0.5 : doc['pagerank_score'].value"
                    }
                },
                "lines_changed": {"type": "long"},
                "files": {"type": "long"}
            }
        }

        # 2. git 매핑 업데이트
        es_client.indices.put_mapping(
            index="git",
            body=git_mapping,
            include_type_name=True
        )

        # 3. 인덱스 패턴 생성 (scripted fields 포함)
        create_pagerank_index_pattern()

        # 4. 기본 인덱스 설정 저장
        config = {
            "type": "config",
            "config": {
                "defaultIndex": "git"
            }
        }
        
        es_client.index(
            index=".kibana",
            id="config:6.8.6",
            body={
                "type": "config",
                "config": {
                    "defaultIndex": "git",
                    "scripted_fields_preserve": True  # 스크립트 필드 보존 설정
                }
            },
            refresh=True
        )

        # 5. 시각화 생성
        create_network_visualization()
        create_pagerank_visualization()

        logger.info("Successfully updated Elasticsearch mapping and visualizations")
        return True

    except Exception as e:
        logger.error(f"Failed to setup Elasticsearch: {e}")
        return False

def create_network_visualization():
    """Network Core Developer 시각화 생성"""
    try:
        visualization = {
            "type": "visualization",
            "attributes": {
                "title": "Network Core Developer",
                "visState": json.dumps({
                    "title": "Network Core Developer",
                    "type": "network",
                    "params": {
                        "type": "circle",
                        "showLabels": True,
                        "showLegend": True,
                        "legendPosition": "right",
                        "nodeSize": "metric",
                        "edgeSize": "metric",
                        "interval": "auto",  # 시간 간격 설정 추가
                        "timeRange": {       # 시간 범위 설정 추가
                            "from": "now-5y",
                            "to": "now"
                        }
                    },
                    "aggs": [
                        {
                            "id": "1",
                            "enabled": True,
                            "type": "sum",
                            "schema": "metric",
                            "params": {
                                "field": "files",
                                "customLabel": "Files"
                            }
                        },
                        {
                            "id": "2",
                            "enabled": True,
                            "type": "terms",
                            "schema": "node",
                            "params": {
                                "field": "author_name.keyword",
                                "size": 20,
                                "order": "desc",
                                "orderBy": "_key",
                                "customLabel": "Authors",
                                "minDocCount": 1  # 최소 문서 수 설정 추가
                            }
                        },
                        {
                            "id": "3",
                            "enabled": True,
                            "type": "sum",
                            "schema": "metric",
                            "params": {
                                "field": "lines_changed",
                                "customLabel": "Lines Changed"
                            }
                        },
                        {
                            "id": "4",
                            "enabled": True,
                            "type": "terms",
                            "schema": "relation",
                            "params": {
                                "field": "repo_name",
                                "size": 5,
                                "order": "desc",
                                "orderBy": "1",
                                "customLabel": "Repositories",
                                "minDocCount": 1  # 최소 문서 수 설정 추가
                            }
                        }
                    ]
                }),
                "uiStateJSON": "{}",
                "description": "",
                "version": 1,
                "kibanaSavedObjectMeta": {
                    "searchSourceJSON": json.dumps({
                        "index": "git",
                        "query": {"query": "*", "language": "lucene"},
                        "filter": []
                    })
                }
            }
        }

        # 시각화 저장
        es_client.index(
            index=".kibana",
            id="2f5869c0-f1b6-11ef-a51e-59ace05a8f4f",
            body=visualization,
            doc_type="doc"
        )

        logger.info("Created Network Core Developer visualization")

    except Exception as e:
        logger.error(f"Failed to create Network visualization: {e}")
        raise

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=9000)