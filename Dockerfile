# Use the official Python 3.11 image as the base image
FROM python:3.11-slim

# Install dependencies
RUN apt-get update && \
    apt-get install -y curl unzip && \
    rm -rf /var/lib/apt/lists/*

# Install AWS CLI v2
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip" && \
    unzip awscliv2.zip && \
    ./aws/install && \
    rm -rf awscliv2.zip aws

# Set the working directory
WORKDIR /action

# Copy the action and src files
COPY action.yml /action/action.yml
COPY src/ /action/src/

# Install Python dependencies
RUN pip install -r /action/src/requirements.txt

# Set the entrypoint to run the Python script as a module
ENTRYPOINT ["python3", "-m", "src.cli"]
