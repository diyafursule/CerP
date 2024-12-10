#!/bin/bash
#SBATCH --job-name=visdom_server
#SBATCH --partition=small
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --gres=gpu:1
#SBATCH --time=2:00:00
#SBATCH --output=visdom_server.out

module load python/3.10.pytorch
python -m visdom.server -p 8098

