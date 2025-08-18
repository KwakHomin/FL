# Step 1: Base Image Selection
# Use NVIDIA's official L4T (Linux for Tegra) BASE image for JetPack 6.2.1 (L4T R36.3).
# This is the pure OS image, providing the perfect foundation.
FROM nvcr.io/nvidia/l4t-base:r36.2.0

# Step 2: Set environment variables to prevent interactive prompts during build
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Seoul

# Step 3: Install essential system dependencies like Python and pip
# As we are using a base image, we need to install these ourselves.
RUN apt-get update && \
    apt-get install -y python3-pip sudo && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Step 4: Create a non-root user and add to the 'gpio' group
# [cite_start]This is a security best practice and necessary for GPIO access. [cite: 353]
RUN groupadd gpio && \
    useradd -m -s /bin/bash -G sudo,gpio,video jetsonuser && \
    echo "jetsonuser ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/jetsonuser

USER jetsonuser
WORKDIR /home/jetsonuser/app

# Step 5: Install PyTorch and Torchvision for Jetson
# [cite_start]This is the most critical step, taken directly from your manual's logic. [cite: 130, 143]
# We install torch/torchvision from the special NVIDIA-provided index URL.
RUN python3 -m pip install --no-cache-dir torch torchvision --index-url https://pypi.jetson-ai-lab.io/jp6/cu126

# Step 6: Copy and install Python dependencies
# IMPORTANT: Your requirements.txt should NOT include torch, torchvision, or tensorrt.
COPY --chown=jetsonuser:jetsonuser requirements.txt .
RUN python3 -m pip install --no-cache-dir -r requirements.txt

# Step 7: Copy your application code into the container
COPY --chown=jetsonuser:jetsonuser . .

# Step 8: Set the default command to run when the container starts
# Replace 'your_app.py' with the actual name of your main script.
CMD ["python3", "jetson_server.py"]
