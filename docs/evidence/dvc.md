# DVC evidence

Exported 2026-11-15 18:33 UTC

## Tracked dataset pointer (`data/dataset.csv.dvc`)
```yaml
outs:
- md5: 9c8da426164432cf5dd9a9333f560bd1
  size: 42646
  hash: md5
  path: dataset.csv
```

## Configured remote
```
localremote     I:\MLPAss\dvc-remote    (default)
```

## Working tree vs recorded data (`dvc status`)
```
train:
	changed outs:
		modified:           models\model.joblib
		modified:           metrics.json
```

## Pipeline stage lock (`dvc.lock`)
```yaml
schema: '2.0'
stages:
  train:
    cmd: python training/train.py --data data/dataset.csv --experiment 
      failure-prediction
    deps:
    - path: data/dataset.csv
      hash: md5
      md5: 9c8da426164432cf5dd9a9333f560bd1
      size: 42646
    - path: training/train.py
      hash: md5
      md5: daeff8739bd8fabc4bd6fc11c5ef4fe2
      size: 8268
    outs:
    - path: metrics.json
      hash: md5
      md5: 6423ea13aa51b382da4f164657de9357
      size: 644
    - path: models/model.joblib
      hash: md5
      md5: 865ca302aef4bfb634c19ecc7d906629
      size: 2383977
```

## Git history of the pointer file
```
1c01f0c 2026-11-15 feat: add dataset generator and DVC tracking with a local remote
```

## Tags
```
dataset-v1
```

## Content stored in the DVC remote
```
<remote>/files/md5/9c/8da426164432cf5dd9a9333f560bd1
```
