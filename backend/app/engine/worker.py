"""
arq WorkerSettings — Worker 进程入口。

启动命令: arq app.engine.worker.WorkerSettings
"""
from arq.connections import RedisSettings

from app.deps.worker import get_redis_settings
from app.engine.tasks.git_sync import run_git_sync
from app.engine.tasks.execution import run_automated_execution
from app.engine.tasks.page_survey import run_page_survey


class WorkerSettings:
    """arq Worker 配置。"""
    # **新任务函数必须加进这个列表**，否则 enqueue 之后既不跑也不报错，
    # 一直躺在 redis 里 —— 页面上看着是「一直在跑」，查不出原因。
    functions = [run_git_sync, run_automated_execution, run_page_survey]
    redis_settings: RedisSettings = get_redis_settings()
    max_jobs = 6
    job_timeout = 600
