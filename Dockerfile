# Use the official Python 3.11 image as the base image
FROM python:3.11-slim

# Install dependencies
RUN apt-get update && \
    apt-get install -y curl unzip && \
    rm -rf /var/lib/apt/lists/*

# Install Docker CLI
RUN curl -fsSL https://download.docker.com/linux/debian/gpg | apt-key add - && \
    add-apt-repository "deb [arch=amd64] https://download.docker.com/linux/debian $(lsb_release -cs) stable" && \
    apt-get update && \
    apt-get install -y docker-ce-cli && \
    rm -rf /var/lib/apt/lists/*

# Install AWS CLI v2
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip" && \
    unzip -q awscliv2.zip && \
    ./aws/install && \
    rm -rf awscliv2.zip aws

# Set the working directory
WORKDIR /action
# Set the PYTHONPATH to include the /action directory
ENV PYTHONPATH /action

# Copy the action and src files
COPY action.yaml ./action.yaml
COPY src/ ./src/

# Install Python dependencies
RUN pip install -r ./src/requirements.txt -q

# Set the entrypoint to run the Python script as a module
ENTRYPOINT ["python", "-m", "src.cli"]
