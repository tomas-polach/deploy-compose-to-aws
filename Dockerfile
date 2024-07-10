# Use the official Python 3.11 slim image as the base image
FROM python:3.11-slim

# Install dependencies including Docker CLI and Git
RUN apt-get update && \
    apt-get install -y \
    curl \
    unzip \
    gnupg \
    lsb-release \
    software-properties-common \
    git && \
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null && \
    apt-get update && \
    apt-get install -y docker-ce-cli && \
    rm -rf /var/lib/apt/lists/*

# Install AWS CLI v2
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip" && \
    unzip -q awscliv2.zip && \
    ./aws/install && \
    rm -rf awscliv2.zip aws

# Install docker-compose
RUN curl -L "https://github.com/docker/compose/releases/download/1.29.2/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose && \
    chmod +x /usr/local/bin/docker-compose

# Verify docker-compose installation
RUN docker-compose --version

# Set the working directory
WORKDIR /action

# Set the PYTHONPATH to include the /action directory
ENV PYTHONPATH /action

# Copy the action and src files
COPY action.yaml ./action.yaml
COPY requirements.txt ./requirements.txt
COPY src/ ./src/

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt -q

# Set the entrypoint to run the Python script as a module
ENTRYPOINT ["python", "-m", "src.cli"]
