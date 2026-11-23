# What an AI assistant typically returns for:
#   "Write a Lambda that processes cancellation events and decides refunds."
# with NO repository context. Kept as a review exercise - DO NOT DEPLOY.
#
# Find the defects. Classify each one: hallucinated API | missing error path |
# over-abstraction | payload logging | wrong business rule | silent failure | other.
# Then run it:   python3 day2/demo/weak_cancellation.py

import json
import logging
from abc import ABC, abstractmethod

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)
sqs = boto3.client("sqs", region_name="ap-south-1")

PENALTY = 0.25


class AbstractRefundStrategy(ABC):
    @abstractmethod
    def calculate(self, order):
        pass


class StandardRefundStrategy(AbstractRefundStrategy):
    def calculate(self, order):
        if order["fare_class"] in ["Y", "B", "M", "J", "C", "D"]:
            return order["amount"]
        return round(order["amount"] * (1 - PENALTY), 2)


class RefundStrategyFactory:
    @staticmethod
    def create(kind="standard"):
        return StandardRefundStrategy()


def lambda_handler(event, context):
    logger.info(f"Processing event: {json.dumps(event)}")
    results = []
    for record in event["Records"]:
        order = json.loads(record["body"], strict_mode=True)
        order["amount"] = float(order["fare_amount"])
        logger.info(f"Order for {order['passenger_name']} ({order['contact_email']}) amount {order['amount']}")
        strategy = RefundStrategyFactory.create()
        refund = strategy.calculate(order)
        try:
            sqs.send_message(QueueUrl="https://sqs.ap-south-1.amazonaws.com/123456789012/refunds",
                             MessageBody=json.dumps({"user_id": order["pnr"], "refund": refund}))
        except Exception:
            pass
        results.append({"order_id": order["event_id"], "refund": refund})
    return {"statusCode": 200, "body": json.dumps(results)}


if __name__ == "__main__":
    import pathlib
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    data = pathlib.Path(__file__).resolve().parents[1] / "data" / "cancellation_events.json"
    print(lambda_handler(json.loads(data.read_text()), None))
