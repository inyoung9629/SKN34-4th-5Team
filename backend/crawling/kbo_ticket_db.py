from common.cron_ticket import collect_tickets


if __name__ == "__main__":
    policies, prices, vectors = collect_tickets()
    print(f"티켓 적재 완료: 정책={policies}, 가격={prices}, 벡터={vectors}")
