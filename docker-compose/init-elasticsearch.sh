#!/bin/bash

# Elasticsearch가 준비될 때까지 대기
until curl -s http://elasticsearch:9200 > /dev/null; do
    echo 'Waiting for Elasticsearch...'
    sleep 1
done

# 기본 인덱스 템플릿 생성
curl -X PUT "http://elasticsearch:9200/_template/default" -H 'Content-Type: application/json' -d'
{
  "index_patterns": ["*"],
  "settings": {
    "number_of_shards": 1,
    "number_of_replicas": 0,
    "mapping.total_fields.limit": 2000,
    "mapping.depth.limit": 20,
    "mapping.nested_fields.limit": 50
  }
}'

# .kibana 인덱스 생성 및 매핑 설정
curl -X PUT "http://elasticsearch:9200/.kibana" -H 'Content-Type: application/json' -d'
{
  "settings": {
    "number_of_shards": 1,
    "number_of_replicas": 0
  }
}'

# .kibana 인덱스 매핑 설정
curl -X PUT "http://elasticsearch:9200/.kibana/_mapping" -H 'Content-Type: application/json' -d'
{
  "dynamic": true,
  "properties": {
    "metadashboard": {
      "type": "object",
      "dynamic": true
    }
  }
}'

echo "Elasticsearch initialization completed" 