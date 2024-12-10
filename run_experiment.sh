#!/bin/bash
# Job name:
#SBATCH --job-name=test
# Partition:
#SBATCH --partition=small  # Replace 'small' with the actual partition name if different
#SBATCH --nodes=1
#SBATCH --ntasks=1
## Processors per task:
#SBATCH --cpus-per-task=2
#
#SBATCH --gres=gpu:1 ## Define number of GPUs
#SBATCH --mail-user=fursule.1@iitj.ac.in  # Email address for notifications

#
## Command(s) to run (example):
module load python/3.10.pytorch

python main.py --params utils/mkrum.yaml
