import asyncio
import httpx
import time
import random
import statistics

async def send_request(client, url):
    req_param = random.choice(["dis", "rate", "price"])
    lat = 38.0235 + (random.random() * 481 - 240.5) / 1000.0
    lon = -122.095 + (random.random() * 325 - 157.0) / 1000.0
    full_url = f"{url}?require={req_param}&lat={lat}&lon={lon}"
    
    start = time.perf_counter()
    try:
        response = await client.get(full_url)
        latency = time.perf_counter() - start
        return latency, response.status_code
    except Exception:
        return time.perf_counter() - start, 0

async def main():
    url = "http://localhost:5000/recommendations"
    rps = 500
    duration = 15
    # total_requests = rps * duration
    
    print(f"Starting load test: {rps} RPS for {duration}s to {url}")
    
    latencies = []
    status_codes = {}
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        start_time = time.perf_counter()
        for i in range(duration):
            loop_start = time.perf_counter()
            tasks = [send_request(client, url) for _ in range(rps)]
            results = await asyncio.gather(*tasks)
            
            for lat, code in results:
                latencies.append(lat)
                status_codes[code] = status_codes.get(code, 0) + 1
            
            elapsed = time.perf_counter() - loop_start
            wait_time = max(0, 1.0 - elapsed)
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            
            print(f"Second {i+1} completed. Latencies: avg={statistics.mean([r[0] for r in results if r[1] != 0]):.4f}s")

    end_time = time.perf_counter()
    total_duration = end_time - start_time
    
    # success_latencies = [l for l in latencies if l > 0] # simplified
    avg_latency = statistics.mean(latencies) * 1000
    p99_latency = statistics.quantiles(latencies, n=100)[98] * 1000
    
    print("\n--- Results ---")
    print(f"Total Requests: {len(latencies)}")
    print(f"Duration: {total_duration:.2f}s")
    print(f"Requests/sec: {len(latencies)/total_duration:.2f}")
    print(f"Avg Latency: {avg_latency:.2f}ms")
    print(f"P99 Latency: {p99_latency:.2f}ms")
    print("Status Codes:")
    for code, count in status_codes.items():
        print(f"  {code}: {count}")

if __name__ == "__main__":
    asyncio.run(main())
