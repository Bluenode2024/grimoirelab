import requests
import json
import time
import logging

logger = logging.getLogger(__name__)

def setup_elasticsearch():
    """Elasticsearch 초기 설정"""
    ES_URL = "http://elasticsearch:9200"
    
    # Elasticsearch가 준비될 때까지 대기
    for _ in range(30):
        try:
            response = requests.get(ES_URL)
            if response.status_code == 200:
                break
        except:
            time.sleep(1)
    
    try:
        # 1. Kibana 인덱스 설정
        kibana_settings = {
            "settings": {
                "number_of_shards": 1,
                "number_of_replicas": 0,
                "index.mapping.total_fields.limit": 2000
            },
            "mappings": {
                "properties": {
                    "type": {"type": "keyword"},
                    "dashboard": {"type": "keyword"},
                    "title": {"type": "text"},
                    "projectname": {"type": "keyword"},
                    "search": {"type": "keyword"},
                    "visualization": {"type": "keyword"}
                }
            }
        }
        
        # Kibana 인덱스 삭제 (있다면)
        requests.delete(f"{ES_URL}/.kibana")
        
        # Kibana 인덱스 생성
        response = requests.put(
            f"{ES_URL}/.kibana?include_type_name=true",
            json=kibana_settings
        )
        logger.info(f"Kibana index setup response: {response.text}")
        
        # 2. 기본 템플릿 설정
        template = {
            "index_patterns": ["*"],
            "settings": {
                "number_of_shards": 1,
                "number_of_replicas": 0,
                "index.mapping.total_fields.limit": 2000,
                "index.mapping.depth.limit": 20,
                "index.mapping.nested_fields.limit": 50
            },
            "mappings": {
                "dynamic": "true",
                "properties": {
                    "projectname": {"type": "keyword"},
                    "metadata__timestamp": {"type": "date"},
                    "metadata__updated_on": {"type": "date"},
                    "grimoire_creation_date": {"type": "date"},
                    "author_name": {"type": "keyword"},
                    "author_org_name": {"type": "keyword"},
                    "author_uuid": {"type": "keyword"},
                    "title": {"type": "text"},
                    "repository": {"type": "keyword"}
                }
            }
        }
        
        response = requests.put(
            f"{ES_URL}/_template/grimoirelab_template",
            json=template
        )
        logger.info(f"Template setup response: {response.text}")
        
        # 3. 기존 인덱스 업데이트
        indices_response = requests.get(f"{ES_URL}/_cat/indices?format=json")
        indices = [idx["index"] for idx in indices_response.json()]
        
        for index in indices:
            if index.startswith('.'):
                continue
            
            update_mapping = {
                "dynamic": "true",
                "properties": {
                    "projectname": {"type": "keyword"}
                }
            }
            
            response = requests.put(
                f"{ES_URL}/{index}/_mapping?include_type_name=true",
                json={"properties": update_mapping}
            )
            logger.info(f"Updated mapping for {index}: {response.text}")
        
    except Exception as e:
        logger.error(f"Failed to setup Elasticsearch: {str(e)}")
        logger.exception("Detailed error:")
        raise 