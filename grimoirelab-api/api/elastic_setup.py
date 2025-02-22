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
        # .kibana 인덱스 매핑 설정
        kibana_mapping = {
            "settings": {
                "number_of_shards": 1,
                "number_of_replicas": 0
            },
            "mappings": {
                "doc": {  # doc 타입에 대한 매핑
                    "properties": {
                        "type": {"type": "keyword"},
                        "value": {  # value 필드 매핑 추가
                            "properties": {
                                "title": {"type": "text"},
                                "visState": {"type": "text"},
                                "uiStateJSON": {"type": "text"},
                                "description": {"type": "text"},
                                "version": {"type": "integer"},
                                "kibanaSavedObjectMeta": {
                                    "properties": {
                                        "searchSourceJSON": {"type": "text"}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        # .kibana 인덱스 생성 또는 업데이트
        try:
            es_client.indices.create(index=".kibana", body=kibana_mapping)
            logger.info("Created .kibana index with mapping")
        except Exception as e:
            if "resource_already_exists_exception" not in str(e):
                raise
            # 기존 인덱스가 있다면 매핑 업데이트
            es_client.indices.put_mapping(
                index=".kibana",
                doc_type="doc",
                body=kibana_mapping["mappings"]["doc"]
            )
            logger.info("Updated .kibana index mapping")
        
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