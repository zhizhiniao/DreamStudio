"""Load bundled model bytes without native Windows Unicode-path limitations."""
from importlib.resources import files
import io


def load_silero_model():
    import torch
    torch.set_num_threads(1)
    data=files('silero_vad.data').joinpath('silero_vad.jit').read_bytes()
    model=torch.jit.load(io.BytesIO(data),map_location='cpu')
    model.eval()
    return model
