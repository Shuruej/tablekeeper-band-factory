FROM public.ecr.aws/docker/library/python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f
WORKDIR /service
COPY app ./app
COPY tools ./tools
COPY vendor ./vendor
ENV PYTHONPATH=/service/vendor
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "-m", "app.server"]
