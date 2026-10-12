from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
from unittest.mock import Mock
import time
import httpx
from django.test import SimpleTestCase
from travel.public_page_reader import PublicReader
from llm.v1.rag.course.quality_research import ProfileReader


class ConcurrentReaderTests(SimpleTestCase):
    def test_distinct_pages_overlap_without_duplicating_robots(self):
        barrier, lock, paths = Barrier(2), Lock(), []
        def handle(request):
            with lock: paths.append(request.url.path)
            if request.url.path == "/robots.txt": return httpx.Response(404)
            barrier.wait(timeout=3)
            return httpx.Response(200, text="<title>가게</title>" + "실제 메뉴 원문 " * 40,
                                  headers={"content-type": "text/html"})
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            reader=PublicReader(client, dns_check=lambda _: True, gap=0)
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda path: reader.read("https://www.diningcode.com/"+path, [], complete_text=True), ["a","b"]))
        self.assertTrue(all(r["body_read"] for r in results))
        self.assertEqual(paths.count("/robots.txt"),1)
        self.assertEqual(reader.http_requests,3)

    def test_same_profile_is_single_flight_and_reused(self):
        started, release=Event(),Event()
        def read(*args,**kwargs):
            started.set();self.assertTrue(release.wait(3));return {"body_read":True}
        underlying=Mock(read=Mock(side_effect=read))
        reader=ProfileReader(underlying)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(reader.read,"same",[])
            self.assertTrue(started.wait(3))
            b=pool.submit(reader.read,"same",[])
            release.set()
            self.assertEqual(a.result(),b.result())
        underlying.read.assert_called_once()
        reader.close()
        self.assertEqual(reader.pages,{})

    def test_parallel_requests_share_budget_and_rate_limit(self):
        starts=[]
        def handle(request):
            starts.append(time.monotonic());return httpx.Response(200,text="ok")
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            reader=PublicReader(client,dns_check=lambda _:True,gap=.04,max_requests=2)
            reader._delays["www.diningcode.com"]=.06
            def fetch(i):
                try:return reader.get(f"https://www.diningcode.com/{i}",100)[0]
                except ValueError:return "budget"
            with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(fetch,range(3)))
        self.assertEqual(results.count(200),2)
        self.assertEqual(reader.http_requests,2)
        self.assertGreaterEqual(starts[1]-starts[0],.05)

    def test_block_and_expired_deadline_prevent_later_requests(self):
        calls=[]
        def handle(request):calls.append(request);return httpx.Response(429)
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            reader=PublicReader(client,dns_check=lambda _:True,gap=0)
            self.assertEqual(reader.get("https://www.diningcode.com/a",100)[0],429)
            with self.assertRaises(ValueError):reader.get("https://www.diningcode.com/b",100)
            reader.deadline=time.monotonic()-1
            with self.assertRaises(ValueError):reader.get("https://polle.com/a",100)
        self.assertEqual(len(calls),1)
