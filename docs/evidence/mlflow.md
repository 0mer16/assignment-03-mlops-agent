# MLflow evidence

Exported 2026-11-15 18:33 UTC from http://127.0.0.1:5001

## Experiments
```json
{
    "experiments": [
        {
            "experiment_id": "1",
            "name": "failure-prediction",
            "artifact_location": "/home/umer/mlops/mlartifacts/1",
            "lifecycle_stage": "active",
            "last_update_time": 1794763678974,
            "creation_time": 1794763678974,
            "workspace": "default"
        },
        {
            "experiment_id": "0",
            "name": "Default",
            "artifact_location": "/home/umer/mlops/mlartifacts/0",
            "lifecycle_stage": "active",
            "last_update_time": 1794763627463,
            "creation_time": 1794763627463,
            "workspace": "default"
        }
    ]
}
```

## Runs with their dataset provenance
```
run_name              status    f1_score  dataset       dvc_hash
airflow-20261115-183204FINISHED  0.7733    dataset-v1    9c8da426164432cf
legendary-mule-443    FINISHED  0.7733    dataset-v1    9c8da426164432cf
airflow-20261115-182301FINISHED  0.7733    dataset-v1    9c8da426164432cf
peaceful-dolphin-778  FINISHED  0.7733    dataset-v1    9c8da426164432cf
airflow-20261115-173520FINISHED  0.7733    untracked     
rf-unbounded          FINISHED  0.7826    dataset-v1    9c8da426164432cf
rf-depth8             FINISHED  0.7973    dataset-v1    9c8da426164432cf
rf-depth6             FINISHED  0.8143    dataset-v1    9c8da426164432cf
baseline-depth3       FINISHED  0.8190    dataset-v1    9c8da426164432cf
diag                  FAILED    0.8190    dataset-v1    9c8da426164432cf
baseline-depth3       FAILED    0.8190    dataset-v1    9c8da426164432cf
```

## Model registry
```json
{
    "registered_models": [
        {
            "name": "failure-prediction",
            "creation_timestamp": 1794764356154,
            "last_updated_timestamp": 1794767540395,
            "latest_versions": [
                {
                    "name": "failure-prediction",
                    "version": "3",
                    "creation_timestamp": 1794767540395,
                    "last_updated_timestamp": 1794767540395,
                    "current_stage": "None",
                    "description": "",
                    "source": "runs:/b46fbc85dbf446dabe71f0e8bd991e85/model",
                    "run_id": "b46fbc85dbf446dabe71f0e8bd991e85",
                    "status": "READY",
                    "tags": [
                        {
                            "key": "promoted_by",
                            "value": "training_pipeline"
                        }
                    ],
                    "run_link": ""
                }
            ]
        }
    ]
}
```
