# Use the official Python image from Docker Hub
FROM python:3.12-slim

# Set the working directory
WORKDIR /app

# Copy files needed for installation
COPY pyproject.toml README.md ./
COPY src/ src/

# Install dependencies
RUN pip install --no-cache-dir .

# Copy scripts
COPY scripts/ scripts/

# Copy and run the script to generate the .env file if it doesn't exist
RUN chmod +x scripts/generate_secret.sh && ./scripts/generate_secret.sh

# Create directories for runtime data
RUN mkdir -p uploads clipboard

# Make port 3000 available to the world outside this container
EXPOSE 3000

# Run the application with gunicorn
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:3000", "clipdrop.app:create_app()"]
