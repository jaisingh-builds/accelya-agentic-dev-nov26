"""Classroom stub for the weak-handler demo: stands in for boto3 so running the demo
never creates a real SDK client or reaches AWS. Review the demo as if it used real boto3."""


class _StubSQS:
    def send_message(self, **_kwargs):
        return {"MessageId": "stub-not-sent"}


class boto3:  # noqa: N801 - mimics the module name used in the demo
    @staticmethod
    def client(service, **_kwargs):
        if service != "sqs":
            raise ValueError("classroom stub only provides sqs")
        return _StubSQS()
