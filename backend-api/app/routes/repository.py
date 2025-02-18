# backend-api/app/routes/repository.py
from flask import Blueprint, request, jsonify
import requests
import os
import json
import logging

repo_blueprint = Blueprint('repository', __name__)

GRIMOIRELAB_API_URL = os.getenv('GRIMOIRELAB_API_URL', 'http://grimoirelab-api:9000')

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@repo_blueprint.route('/api/repository', methods=['POST'])
def add_repository():
    before_update = None
    after_update = None
    
    try:
        new_repo_data = request.json
        logger.info(f"Received repository data: {json.dumps(new_repo_data, indent=2)}")
        
        # 입력 데이터 검증 및 변환
        validated_data = validate_repo_data(new_repo_data)
        if not validated_data:
            return jsonify({"error": "Invalid repository data"}), 400
        
        logger.info(f"Validated data: {json.dumps(validated_data, indent=2)}")
        
        # projects.json 파일 내용 확인 (업데이트 전)
        try:
            with open('/default-grimoirelab-settings/projects.json', 'r') as f:
                before_update = json.load(f)
            logger.info(f"Current projects.json content: {json.dumps(before_update, indent=2)}")
        except Exception as e:
            logger.warning(f"Failed to read projects.json before update: {e}")
            before_update = {}
        
        # GrimoireLab API 호출
        response = requests.post(
            f"{GRIMOIRELAB_API_URL}/update-projects",
            json=validated_data,
            timeout=10
        )
        
        logger.info(f"GrimoireLab API response: {response.status_code} - {response.text}")
        
        if response.status_code != 200:
            return jsonify({
                "error": "Failed to update projects",
                "details": response.json()
            }), 500
        
        # projects.json 파일 내용 확인 (업데이트 후)
        try:
            with open('/default-grimoirelab-settings/projects.json', 'r') as f:
                after_update = json.load(f)
            logger.info(f"Updated projects.json content: {json.dumps(after_update, indent=2)}")
        except Exception as e:
            logger.warning(f"Failed to read updated projects.json: {e}")
            after_update = {}
            
        return jsonify({
            "message": "Repository added successfully",
            "before": before_update,
            "after": after_update,
            "api_response": response.json()
        })
        
    except requests.exceptions.ConnectionError:
        return jsonify({
            "error": "Cannot connect to GrimoireLab service",
            "url": GRIMOIRELAB_API_URL
        }), 503
    except Exception as e:
        logger.error(f"Error in add_repository: {str(e)}")
        return jsonify({
            "error": str(e),
            "before": before_update,
            "after": after_update
        }), 500

@repo_blueprint.route('/api/repository/test', methods=['GET'])
def test_connection():
    try:
        response = requests.get(f"{GRIMOIRELAB_API_URL}/health")
        return jsonify({
            "grimoirelab_url": GRIMOIRELAB_API_URL,
            "connection_status": "success" if response.status_code == 200 else "failed",
            "response": response.json() if response.status_code == 200 else None
        })
    except requests.exceptions.ConnectionError:
        return jsonify({
            "grimoirelab_url": GRIMOIRELAB_API_URL,
            "connection_status": "failed",
            "error": "Connection refused"
        })

def validate_repo_data(data):
    # 기존 형식이면 새 형식으로 변환
    if 'meta' in data and 'git' in data:
        project_id = data['meta'].get('title', 'default').lower().replace(' ', '-')
        return {
            project_id: {
                'meta': data['meta'],
                'git': data['git']
            }
        }
    
    # 새 형식 검증
    if not isinstance(data, dict):
        return None
    
    for project_data in data.values():
        if not isinstance(project_data, dict):
            return None
        if not all(field in project_data for field in ['meta', 'git']):
            return None
    
    return data