import json, os, sys
import torch

sys.path.insert(0, os.path.dirname(__file__))
from smartqueue_ranker import SmartQueueRanker

OUT = os.path.join(os.path.dirname(__file__), "..", "model_artifacts")
os.makedirs(OUT, exist_ok=True)

model = SmartQueueRanker()
model.eval()

torch.save(model, os.path.join(OUT, "smartqueue_ranker.pt"))

torch.onnx.export(
    model, torch.randn(1, 64),
    os.path.join(OUT, "smartqueue_ranker.onnx"),
    input_names=["input"], output_names=["output"],
    dynamic_axes={"input": {0: "batch_size"}, "output": {0: "batch_size"}},
    opset_version=17,
)

with open(os.path.join(OUT, "model_info.json"), "w") as f:
    json.dump({"model_version": "1.0.0", "user_feat_dim": 32, "song_feat_dim": 32, "input_dim": 64}, f, indent=2)

print("model_artifacts/ created:")
for fn in os.listdir(OUT):
    print(f"  {fn}")
