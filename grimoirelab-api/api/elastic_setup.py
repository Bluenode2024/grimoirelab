import requests
import json
import time
import logging
import os
from elasticsearch import Elasticsearch

logger = logging.getLogger(__name__)

# Elasticsearch 클라이언트 초기화
ES_URL = os.getenv('ES_URL', 'http://elasticsearch:9200')
es_client = Elasticsearch([ES_URL])

def setup_elasticsearch():
    """Elasticsearch 초기 설정"""
    
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
        
        # git 인덱스 매핑 추가
        git_mapping = {
            "settings": {
                "number_of_shards": 1,
                "number_of_replicas": 0
            },
            "mappings": {
                "properties": {
                    "origin": {"type": "keyword"},
                    "repository": {"type": "keyword"},
                    "author_name": {"type": "text"},
                    "author_name.keyword": {"type": "keyword"},
                    "author_org_name": {"type": "keyword"},
                    "author_uuid": {"type": "keyword"},
                    "hash": {"type": "keyword"},
                    "message": {"type": "text"},
                    "grimoire_creation_date": {"type": "date"},
                    "author_date": {"type": "date"},
                    "committer_date": {"type": "date"},
                    "lines_added": {"type": "long"},
                    "lines_removed": {"type": "long"},
                    "files": {"type": "long"}
                }
            }
        }

        # git 인덱스 생성
        try:
            es_client.indices.create(index="git", body=git_mapping)
            logger.info("Created git index with mapping")
        except Exception as e:
            if "resource_already_exists_exception" not in str(e):
                raise
            logger.info("Git index already exists")

    except Exception as e:
        logger.error(f"Failed to setup Elasticsearch: {str(e)}")
        logger.exception("Detailed error:")
        raise 

def setup_elasticsearch_mappings():
    try:
        # git 인덱스 패턴 설정
        git_pattern = {
            "type": "index-pattern",
            "index-pattern": {
                "title": "git*",
                "timeFieldName": "grimoire_creation_date"
            },
            "fields": json.dumps([
                {"name": "origin", "type": "string", "count": 0, "scripted": False, "indexed": True, "analyzed": False, "doc_values": True},
                {"name": "author_name", "type": "string", "count": 0, "scripted": False, "indexed": True, "analyzed": True, "doc_values": False},
                {"name": "author_name.keyword", "type": "string", "count": 0, "scripted": False, "indexed": True, "analyzed": False, "doc_values": True},
                {"name": "hash", "type": "string", "count": 0, "scripted": False, "indexed": True, "analyzed": False, "doc_values": True},
                {"name": "grimoire_creation_date", "type": "date", "count": 0, "scripted": False, "indexed": True, "analyzed": False, "doc_values": True},
                {"name": "lines_added", "type": "long", "count": 0, "scripted": False, "indexed": True, "analyzed": False, "doc_values": True},
                {"name": "lines_removed", "type": "long", "count": 0, "scripted": False, "indexed": True, "analyzed": False, "doc_values": True},
                {"name": "files", "type": "long", "count": 0, "scripted": False, "indexed": True, "analyzed": False, "doc_values": True}
            ])
        }
        
        # 인덱스 패턴 생성/업데이트
        try:
            es_client.update(
                index=".kibana",
                id="index-pattern:git",
                body={"doc": git_pattern},
                doc_type="doc"
            )
        except Exception:
            es_client.index(
                index=".kibana",
                id="index-pattern:git",
                body=git_pattern,
                doc_type="doc"
            )

        # 기본 인덱스 패턴으로 설정
        default_index = {
            "type": "config",
            "config": {
                "defaultIndex": "git"
            }
        }

        try:
            es_client.update(
                index=".kibana",
                id="config:v6.8.6",
                body={"doc": default_index},
                doc_type="doc"
            )
        except Exception:
            es_client.index(
                index=".kibana",
                id="config:v6.8.6",
                body=default_index,
                doc_type="doc"
            )

        logger.info("Successfully created git index pattern")
    except Exception as e:
        logger.error(f"Failed to setup elasticsearch mappings: {e}") 