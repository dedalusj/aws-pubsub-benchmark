import os
from urllib.parse import urlparse

from locust import HttpLocust, TaskSet, task

url = urlparse(os.getenv('URL'))


class FireMessages(TaskSet):
    @task
    def profile(self):
        self.client.get(url.path)


class BenchmarkRequest(HttpLocust):
    host = f'{url.scheme}://{url.netloc}'
    task_set = FireMessages
    min_wait = 0
    max_wait = 0
