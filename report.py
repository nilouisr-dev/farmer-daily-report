name: Daily Farmer Report

on:
  schedule:
    # 07:30 Thai time (UTC+7) = 00:30 UTC
    - cron: '30 0 * * *'
  workflow_dispatch: # Allow manual trigger

jobs:
  report:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Generate and push report
        env:
          WORKER_URL: ${{ secrets.WORKER_URL }}
        run: python report.py
