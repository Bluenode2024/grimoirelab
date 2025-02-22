from typing import Dict, List
import networkx as nx
from github import Github
import os
import datetime
import logging

logger = logging.getLogger(__name__)

class CodeQualityAnalyzer:
    def __init__(self, repo_path: str):
        try:
            if not os.getenv('GITHUB_TOKEN'):
                raise ValueError("GitHub token not found")
            self.repo_path = repo_path
            self.g = Github(os.getenv('GITHUB_TOKEN'))
            self.repo = self.g.get_repo(repo_path)
        except Exception as e:
            logger.error(f"Failed to initialize analyzer for {repo_path}: {e}")
            raise

    def get_codeql_metrics(self) -> Dict:
        """
        CodeQL 분석 결과를 가져옵니다
        """
        try:
            logger.info(f"Accessing repository: {self.repo_path}")
            # CodeQL이 없는 경우 기본값 반환
            metrics = {
                'total_alerts': 0,
                'severity_counts': {'critical': 0, 'high': 0, 'medium': 0, 'low': 0},
                'rule_counts': {}
            }
            
            try:
                alerts = self.repo.get_code_scanning_alerts()
                for alert in alerts:
                    metrics['total_alerts'] += 1
                    metrics['severity_counts'][alert.severity.lower()] += 1
                    metrics['rule_counts'][alert.rule.id] = metrics['rule_counts'].get(alert.rule.id, 0) + 1
            except AttributeError:
                logger.warning(f"CodeQL not available for repository: {self.repo_path}")
            
            return metrics
        except Exception as e:
            logger.error(f"Failed to get codeql metrics: {e}")
            return {}

    def calculate_file_importance(self, timeframe_days: int = 30) -> Dict:
        """
        PageRank 알고리즘을 사용하여 파일의 중요도를 계산합니다
        """
        try:
            since = datetime.datetime.now() - datetime.timedelta(days=timeframe_days)
            commits = list(self.repo.get_commits(since=since))
            
            G = nx.DiGraph()
            file_changes = {}
            
            # 파일 변경 횟수 및 관계 추적
            for commit in commits:
                try:
                    files = list(commit.files)
                    
                    # 파일 변경 횟수 추적
                    for file in files:
                        file_changes[file.filename] = file_changes.get(file.filename, 0) + 1
                        
                    # 같은 커밋에서 변경된 파일들 간의 관계 설정
                    for i in range(len(files)):
                        for j in range(i + 1, len(files)):
                            # 파일 크기와 변경 빈도를 고려한 가중치 계산
                            file_i_size = files[i].additions + files[i].deletions
                            file_j_size = files[j].additions + files[j].deletions
                            weight = (file_i_size + file_j_size) / (file_changes[files[i].filename] * file_changes[files[j].filename])
                            G.add_edge(files[i].filename, files[j].filename, weight=weight)
                            G.add_edge(files[j].filename, files[i].filename, weight=weight)
                            
                except Exception as e:
                    logger.error(f"Failed to process commit {commit.sha}: {e}")
                    continue
            
            # PageRank 계산 (가중치 적용)
            if G.nodes:
                pagerank = nx.pagerank(G, weight='weight')
            else:
                pagerank = {}
            
            return {
                'pagerank': pagerank,
                'changes': file_changes
            }
        except Exception as e:
            logger.error(f"Failed to calculate file importance: {e}")
            return {'pagerank': {}, 'changes': {}}

    def get_author_metrics(self, author: str, timeframe_days: int = 30) -> Dict:
        """
        저자의 기여도를 분석합니다
        """
        try:
            since = datetime.datetime.now() - datetime.timedelta(days=timeframe_days)
            commits = list(self.repo.get_commits(author=author, since=since))
            
            file_importance = self.calculate_file_importance(timeframe_days)
            
            metrics = {
                'commit_count': len(commits),  # 실제 커밋 수 계산
                'files_changed': 0,
                'lines_added': 0,
                'lines_deleted': 0,
                'importance_score': 0.0,
                'code_quality_impact': 0,
                'critical_file_changes': 0
            }
            
            for commit in commits:
                try:
                    files = list(commit.files)
                    metrics['files_changed'] += len(files)
                    
                    for file in files:
                        metrics['lines_added'] += file.additions
                        metrics['lines_deleted'] += file.deletions
                        
                        # 파일 중요도 점수 반영
                        importance = file_importance['pagerank'].get(file.filename, 0)
                        metrics['importance_score'] += importance
                        
                        # 중요 파일 변경 횟수
                        if importance > 0.5:  # 중요도 임계값
                            metrics['critical_file_changes'] += 1
                            
                except Exception as e:
                    logger.error(f"Failed to process commit {commit.sha}: {e}")
                    continue
                
            return metrics
        except Exception as e:
            logger.error(f"Failed to get author metrics: {e}")
            return {
                'commit_count': 0,
                'files_changed': 0,
                'lines_added': 0,
                'lines_deleted': 0,
                'importance_score': 0.0,
                'code_quality_impact': 0,
                'critical_file_changes': 0
            } 