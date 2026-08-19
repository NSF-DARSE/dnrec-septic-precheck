# The reviewer console, packaged to run with no AWS credentials and no network.
#
# The console serves cached Textract output from disk, reads the GIS layers from
# data/gis, and evaluates rules from rules_7101.yaml, so a running container needs
# no IAM role and nothing from the environment. That is what makes this
# deployable on a workshop account whose credentials expire in hours: the
# credentials are needed to create the deployment, never to serve it.
#
# Uploading a brand new PDF is the one path that would need Textract. It fails
# with a clear message rather than hanging, which is the existing behaviour.
#
# Build:  docker build -t septic-precheck .
# Run:    docker run --rm -p 8501:8501 septic-precheck
# Then:   http://localhost:8501

FROM python:3.11-slim

# pyproj and shapely ship manylinux wheels, so no compiler is needed. libexpat
# and libgl come in for matplotlib's font and image handling.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libexpat1 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so a code change does not reinstall them. The pins matter:
# numpy stays below 2 because pandas 1.5.3 is compiled against the numpy 1.x C
# API, and matplotlib 3.7.5 is the last line that works against numpy 1.23.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir "streamlit==1.61.1"

COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir -e . --no-deps

# Everything the console reads at runtime.
COPY app.py ./
COPY .streamlit/ ./.streamlit/
COPY data/gis/ ./data/gis/
COPY scripts/ ./scripts/

# rules_7101.yaml ships inside src/septic/rules/ and is already installed above.
# docs/regulations/ is the 245 page PDF and is deliberately not here: the rules
# carry their own quoted text, section and page, so nothing at review time reads
# the regulation itself. It is 6.9 MB of image for nothing.

# The demo packets and only the cache entries they need. The full Textract cache
# is 1.1 GB across 243 documents; these four are 10.2 MB. Build the context with
# scripts/build_image_context.py so this stays true.
COPY docker-context/examples/ ./out/examples/
COPY docker-context/cache/ ./out/cache/textract/
COPY docker-context/reg_graph.json ./out/reg_graph.json

EXPOSE 8501

# Streamlit needs to bind every interface inside a container, and the usage
# telemetry ping is the one thing that would reach the network at runtime.
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    MPLBACKEND=Agg

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health').read()"

CMD ["streamlit", "run", "app.py"]
