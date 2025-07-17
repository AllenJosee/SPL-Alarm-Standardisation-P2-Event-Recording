import torch

if torch.cuda.is_available():
    print(f"CUDA is available. PyTorch can use the GPU.")
    print(f"Number of GPUs: {torch.cuda.device_count()}")
    print(f"Current GPU Name: {torch.cuda.get_device_name(0)}")
else:
    print("CUDA is NOT available. PyTorch will use the CPU.")