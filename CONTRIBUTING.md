# Contributing

Keep the Agent workflow usable without requiring users to run scripts manually. A declared Provider is not considered supported until dependency installation, offline import probing, task execution, result normalization, and failure reporting are all implemented.

Before submitting a change, run:

```bash
python3 -m pip install -r requirements.txt
npm ci
python3 -m unittest discover -s tests -v
npm test
npm audit --audit-level=high
python3 -m py_compile scripts/*.py
node --check scripts/video_gateway.mjs
```

Do not add real credentials, private media, signed URLs, or paid API responses to fixtures. Mock the network boundary and test that approval, route, and output contracts fail closed.
