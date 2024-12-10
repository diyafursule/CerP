#!/bin/sh

# Job name:
#SBATCH --job-name=InfluenceCL-main

# Partition:
#SBATCH --partition=btech
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --output=output.txt           # Standard output file
#SBATCH --error=error.txt             # Standard error file

# Processors per task:
#SBATCH --cpus-per-task=16

# Request one GPU:
#SBATCH --gres=gpu:1

# module spider cuda



# # Load CUDA module (11.7.1 or 11.8.0, depending on compatibility)
# module load cuda/12.2.0-gcc-12.3.0-4pg4hmh


# Activate Miniconda
source $HOME/miniconda/etc/profile.d/conda.sh

# Activate the environment
conda activate ve_cerp

# echo 'installing python'
# conda install python=3.8
python -V
# python3 -V
# y
# echo 'installation done'
# Change to the project directory
cd /csehome/fursule.1/DATA/CerP
pwd
# conda clean --all -y


# Check GPU status
nvidia-smi
# echo 'Checking torch and vision installation...'

# Install PyTorch and related packages with the correct CUDA version
# pip install torchvision

# pip3 install torch torchvision torchaudio
# pip install torch==1.10.1+cu113 torchvision==0.11.2+cu113 torchaudio===0.10.1+cu113 -f https://download.pytorch.org/whl/cu113/torch_stable.html
# conda install -y torch torchvision torchaudio pytorch-cuda -c pytorch -c nvi

# pip install matplotlib
# pip install scikit-learn

# conda install -y -c pytorch pytorch torchvision torchaudio cudatoolkit=12.2 matplotlib scikit-learn 

# pip install visdom


# Check if PyTorch can detect CUDA
echo 'Checking CUDA availability in PyTorch...'
# python -c "import torch; print('CUDA version:', torch.version.cuda)"
python -c "import torch; print('CUDA available:', torch.cuda.is_available())"
echo 'hello 1'
# Start Visdom server in the background
python -m visdom.server -p 8098 &
echo 'hello 2'
# Wait a moment to ensure Visdom is up and running
sleep 10
echo 'hello 3'
# Run the main script
# CUDA_VISIBLE_DEVICES=1 python /csehome/fursule.1/DATA/CerP/main.py --params utils/mkrum.yaml
 python /csehome/fursule.1/DATA/CerP/main.py --params utils/mkrum.yaml
# CUDA_LAUNCH_BLOCKING=0 CUDA_VISIBLE_DEVICES=1 python /csehome/fursule.1/DATA/CerP/main.py --params utils/mkrum.yaml