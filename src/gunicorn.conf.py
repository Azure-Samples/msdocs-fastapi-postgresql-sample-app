import multiprocessing

max_requests = 1000
max_requests_jitter = 50
log_file = "-"
bind = "0.0.0.0:8888"
workers = (multiprocessing.cpu_count() * 2) + 1

worker_class = "my_uvicorn_worker.MyUvicornWorker"

timeout = 600
