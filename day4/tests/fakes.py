"""In-memory S3 and Lambda context for tests. No AWS account, no network, no moto."""
import io
import logging

from botocore.exceptions import ClientError


def client_error(code, status=400, op="GetObject"):
    return ClientError({"Error": {"Code": code, "Message": code},
                        "ResponseMetadata": {"HTTPStatusCode": status}}, op)


class FakeS3:
    def __init__(self):
        self.objects = {}            # (bucket, key) -> {"Body": bytes, "Metadata": {...}}
        self.fail_next = {}          # op name -> ClientError to raise once
        self.puts = []

    def add(self, bucket, key, body, metadata=None):
        self.objects[(bucket, key)] = {"Body": body, "Metadata": metadata or {}}

    def _maybe_fail(self, op):
        err = self.fail_next.pop(op, None)
        if err:
            raise err

    def get_object(self, Bucket, Key):
        self._maybe_fail("get_object")
        if (Bucket, Key) not in self.objects:
            raise client_error("NoSuchKey", 404)
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)]["Body"])}

    def head_object(self, Bucket, Key):
        self._maybe_fail("head_object")
        if (Bucket, Key) not in self.objects:
            raise client_error("404", 404, "HeadObject")
        o = self.objects[(Bucket, Key)]
        return {"Metadata": dict(o["Metadata"]), "ContentLength": len(o["Body"])}

    def put_object(self, Bucket, Key, Body, ContentType=None, Metadata=None):
        self._maybe_fail("put_object")
        self.puts.append(Key)
        self.objects[(Bucket, Key)] = {"Body": Body if isinstance(Body, bytes) else Body.encode(), "Metadata": Metadata or {}}
        return {}

    def keys(self, bucket, prefix=""):
        return sorted(k for (b, k) in self.objects if b == bucket and k.startswith(prefix))

    def text(self, bucket, key):
        return self.objects[(bucket, key)]["Body"].decode("utf-8")


class FakeContext:
    function_name = "accelya-m4-test-processor"
    memory_limit_in_mb = "256"      # a STRING in real Lambda (found in the live rehearsal)
    invoked_function_arn = "arn:aws:lambda:us-east-1:111111111111:function:accelya-m4-test-processor"
    aws_request_id = "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b"


class Capture(logging.Handler):
    """Collect formatted log lines. Pass it to build_logger(..., handler=Capture()), or use capture()."""
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(self.format(record))


def capture(powertools_logger):
    """Silence a Powertools logger's stdout and collect its lines instead (same formatter)."""
    h = powertools_logger.registered_handler
    if isinstance(h, Capture):
        return h
    cap = Capture()
    cap.setFormatter(h.formatter)
    h.setStream(io.StringIO())          # keep test output quiet
    powertools_logger._logger.addHandler(cap)
    return cap
