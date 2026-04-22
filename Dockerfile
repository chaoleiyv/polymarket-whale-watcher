FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ src/
COPY pyproject.toml .

# Create data directories
RUN mkdir -p data reports daily_briefings

# Default command
CMD ["python", "-m", "src.main", "run"]
