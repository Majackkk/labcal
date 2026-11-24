"""Optional local Paddle runtime. JSON pipes only; no model packages in core Python."""
import json
import os
import sys


def main():
    # Paddle and native libraries may print to stdout. Reserve the protocol fd first.
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    def send(value):
        protocol.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
    try:
        config = json.loads(sys.stdin.readline())
        from paddleocr import PaddleOCRVL
        pipeline = PaddleOCRVL(
            pipeline_version="v1.6", device="cpu", enable_mkldnn=False,
            layout_detection_model_dir=config["layout_model"], vl_rec_model_dir=config["vl_model"],
            use_doc_orientation_classify=False, use_doc_unwarping=False, use_queues=False)
        send({"ready": True})
        for line in sys.stdin:
            request = json.loads(line)
            results = pipeline.predict(request["image"], max_new_tokens=4096)
            if len(results) != 1:
                raise ValueError("Expected exactly one page")
            value = results[0].json
            if isinstance(value, str): value = json.loads(value)
            send({"result": value.get("res", value)})
    except Exception as exc:
        send({"error": type(exc).__name__ + ": " + str(exc)[:800]})


if __name__ == "__main__": main()
