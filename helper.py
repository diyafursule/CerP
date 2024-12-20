import heapq
from shutil import copyfile
from collections import defaultdict
import math
import torch
from sklearn.metrics import confusion_matrix
from torch.autograd import Variable
import logging
import sklearn.metrics.pairwise as smp
from torch.nn.functional import log_softmax
import torch.nn.functional as F
import torch.nn as nn
import time
import functools

logger = logging.getLogger("logger")
import os
import json
import numpy as np
import config
import copy
import main
import utils.csv_record
import random


class Helper:
    def __init__(self, current_time, params, name):
        self.current_time = current_time
        self.target_model = None
        self.local_model = None

        self.train_data = None
        self.test_data = None
        self.poisoned_data = None
        self.test_data_poison = None

        self.params = params
        self.name = name
        self.best_loss = math.inf
        aa = self.params['aggregation_methods']
        bb=self.params['attack_methods']
        alpha=self.params['alpha_loss']
        beta=self.params['beta_loss']
        self.folder_path = f'saved_models/model_{self.name}_{current_time}_{bb}_{aa}_{alpha}_{beta}'
        try:
            if not os.path.exists(self.folder_path):
                os.makedirs(self.folder_path)
        except FileExistsError:
            logger.info('Folder already exists')
        logger.addHandler(logging.FileHandler(filename=f'{self.folder_path}/log.txt'))
        logger.addHandler(logging.StreamHandler())
        logger.setLevel(logging.DEBUG)
        logger.info(f'current path: {self.folder_path}')
        if not self.params.get('environment_name', False):
            self.params['environment_name'] = self.name

        self.params['current_time'] = self.current_time
        self.params['folder_path'] = self.folder_path
        self.fg = FoolsGold(use_memory=self.params['fg_use_memory'])

    def save_checkpoint(self, state, is_best, filename='checkpoint.pth.tar'):
        if not self.params['save_model']:
            return False
        torch.save(state, filename)

        if is_best:
            copyfile(filename, 'model_best.pth.tar')

    @staticmethod
    def model_global_norm(model):
        squared_sum = 0
        for name, layer in model.named_parameters():
            squared_sum += torch.sum(torch.pow(layer.data, 2))
        return math.sqrt(squared_sum)

    @staticmethod
    def model_dist_norm(model, target_params):
        squared_sum = 0
        for name, layer in model.named_parameters():
            squared_sum += torch.sum(torch.pow(layer.data - target_params[name].data, 2))
        return math.sqrt(squared_sum)

    @staticmethod
    def model_max_values(model, target_params):
        squared_sum = list()
        for name, layer in model.named_parameters():
            squared_sum.append(torch.max(torch.abs(layer.data - target_params[name].data)))
        return squared_sum

    @staticmethod
    def model_max_values_var(model, target_params):
        squared_sum = list()
        for name, layer in model.named_parameters():
            squared_sum.append(torch.max(torch.abs(layer - target_params[name])))
        return sum(squared_sum)

    @staticmethod
    def get_one_vec(model, variable=False):
        size = 0
        for name, layer in model.named_parameters():
            if name == 'decoder.weight':
                continue
            size += layer.view(-1).shape[0]
        if variable:
            sum_var = Variable(torch.cuda.FloatTensor(size).fill_(0))
        else:
            sum_var = torch.cuda.FloatTensor(size).fill_(0)
        size = 0
        for name, layer in model.named_parameters():
            if name == 'decoder.weight':
                continue
            if variable:
                sum_var[size:size + layer.view(-1).shape[0]] = (layer).view(-1)
            else:
                sum_var[size:size + layer.view(-1).shape[0]] = (layer.data).view(-1)
            size += layer.view(-1).shape[0]

        return sum_var

    @staticmethod
    def model_dist_norm_var(model, target_params_variables, norm=2):
        size = 0
        for name, layer in model.named_parameters():
            size += layer.view(-1).shape[0]
        sum_var = torch.FloatTensor(size).fill_(0)
        sum_var = sum_var.to(config.device)
        size = 0
        for name, layer in model.named_parameters():
            sum_var[size:size + layer.view(-1).shape[0]] = (
                    layer - target_params_variables[name]).view(-1)
            size += layer.view(-1).shape[0]

        return torch.norm(sum_var, norm)

    def cos_sim_loss(self, model, target_vec):
        model_vec = self.get_one_vec(model, variable=True)
        target_var = Variable(target_vec, requires_grad=False)
        # target_vec.requires_grad = False
        cs_sim = torch.nn.functional.cosine_similarity(
            self.params['scale_weights'] * (model_vec - target_var) + target_var, target_var, dim=0)
        # cs_sim = cs_loss(model_vec, target_vec)
        logger.info("los")
        logger.info(cs_sim.data[0])
        logger.info(torch.norm(model_vec - target_var).data[0])
        loss = 1 - cs_sim

        return 1e3 * loss

    def model_cosine_similarity(self, model, target_params_variables,
                                model_id='attacker'):

        cs_list = list()
        cs_loss = torch.nn.CosineSimilarity(dim=0)
        for name, data in model.named_parameters():
            if name == 'decoder.weight':
                continue

            model_update = (data.view(-1) - target_params_variables[name].view(-1)) + target_params_variables[
                name].view(-1)

            cs = F.cosine_similarity(model_update,
                                     target_params_variables[name].view(-1), dim=0)
            cs_list.append(cs)

        cos_los_submit = 1 - (sum(cs_list) / len(cs_list))

        return sum(cs_list) / len(cs_list)

    def accum_similarity(self, last_acc, new_acc):

        cs_list = list()

        cs_loss = torch.nn.CosineSimilarity(dim=0)

        for name, layer in last_acc.items():
            cs = cs_loss(Variable(last_acc[name], requires_grad=False).view(-1),
                         Variable(new_acc[name], requires_grad=False).view(-1))

            cs_list.append(cs)
        cos_los_submit = 1 * (1 - sum(cs_list) / len(cs_list))

        return sum(cos_los_submit)

    @staticmethod
    def clip_weight_norm(model, clip):
        total_norm = Helper.model_global_norm(model)
        logger.info("total_norm: " + str(total_norm) + "clip_norm: " + str(clip))
        max_norm = clip
        clip_coef = max_norm / (total_norm + 1e-6)
        current_norm = total_norm
        if total_norm > max_norm:
            for name, layer in model.named_parameters():
                layer.data.mul_(clip_coef)
            current_norm = Helper.model_global_norm(model)
            logger.info("clip~~~ norm after clipping: " + str(current_norm))
        return current_norm

    @staticmethod
    def dp_noise(param, sigma):

        noised_layer = torch.cuda.FloatTensor(param.shape).normal_(mean=0, std=sigma)

        return noised_layer

    def accumulate_weight(self, weight_accumulator, epochs_submit_update_dict, state_keys, num_samples_dict):
        if self.params['aggregation_methods'] == config.AGGR_FOOLSGOLD or self.params[
            'aggregation_methods'] == config.AGGR_KRUM \
                or self.params['aggregation_methods'] == config.AGGR_TRIMMED_MEAN or self.params[
            'aggregation_methods'] == config.AGGR_BULYAN or \
                self.params['aggregation_methods'] == config.AGGR_DNC or self.params[
            'aggregation_methods'] == config.AGGR_MEDIAN or \
                self.params['aggregation_methods'] == config.AGGR_MKRUM:
            updates = dict()
            for i in range(0, len(state_keys)):
                local_model_gradients = epochs_submit_update_dict[state_keys[i]][0]  # agg 1 interval
                num_samples = num_samples_dict[state_keys[i]]
                updates[state_keys[i]] = (num_samples, copy.deepcopy(local_model_gradients))
            return None, updates

        else:
            updates = dict()
            for i in range(0, len(state_keys)):
                local_model_update_list = epochs_submit_update_dict[state_keys[i]]
                update = dict()
                num_samples = num_samples_dict[state_keys[i]]
                for name, data in local_model_update_list[0].items():
                    update[name] = torch.zeros_like(data)

                for j in range(0, len(local_model_update_list)):
                    local_model_update_dict = local_model_update_list[j]
                    for name, data in local_model_update_dict.items():
                        weight_accumulator[name].add_(local_model_update_dict[name])
                        update[name].add_(local_model_update_dict[name])
                        detached_data = data.cpu().detach().numpy()
                        # print(detached_data.shape)
                        detached_data = detached_data.tolist()
                        # print(detached_data)
                        local_model_update_dict[name] = detached_data  # from gpu to cpu

                updates[state_keys[i]] = (num_samples, update)

            return weight_accumulator, updates

    def init_weight_accumulator(self, target_model):
        weight_accumulator = dict()
        for name, data in target_model.state_dict().items():
            weight_accumulator[name] = torch.zeros_like(data)

        return weight_accumulator

    def average_shrink_models(self, weight_accumulator, target_model, epoch_interval):

        for name, data in target_model.state_dict().items():
            if self.params.get('tied', False) and name == 'decoder.weight':
                continue
            update_per_layer = weight_accumulator[name] * (self.params["eta"] / self.params["no_models"])
            newvalue = data + update_per_layer
            target_model.state_dict()[name].copy_(newvalue)

        return True

    @staticmethod
    def row_into_parameters(row, target_model):
        offset = 0
        for param in target_model.parameters():
            new_size = functools.reduce(lambda x, y: x * y, param.shape)
            current_data = row[offset:offset + new_size]
            param.data[:] = torch.from_numpy(current_data.reshape(param.shape))
            offset += new_size

    # compute L2-norm
    @staticmethod
    def krum_create_distances(users_grads):
        distances = defaultdict(dict)
        for i in range(len(users_grads)):
            for j in range(i):
                distances[i][j] = distances[j][i] = np.linalg.norm(users_grads[i] - users_grads[j])
        return distances

    @staticmethod
    # get the updated weights from users
    def collect_gradients(updates, users_grads):
        users_gradsss = []
        for name, data in updates.items():
            users_gradsss.append(data[1])  # gradient

        for i in range(len(users_gradsss)):
            grads = np.concatenate([users_gradsss[i][j].cpu().numpy().flatten() for j in range(len(users_gradsss[i]))])
            users_grads[i, :] = grads
        return users_grads

    def mkrum(self, target_model, updates, corrupted_count, users_count, distances=None, return_index=False):
        current_weights = np.concatenate([i.data.cpu().numpy().flatten() for i in target_model.parameters()])
        users_grads = np.empty((users_count, len(current_weights)), dtype=current_weights.dtype)
        users_grads = Helper.collect_gradients(updates, users_grads)

        if not return_index:
            assert users_count >= 2 * corrupted_count + 1, (
                'users_count>=2*corrupted_count + 3', users_count, corrupted_count)
        non_malicious_count = users_count - corrupted_count
        selection_set = []
        krumscore = []
        if distances is None:
            distances = Helper.krum_create_distances(users_grads)
        for user in distances.keys():
            errors = sorted(distances[user].values())
            current_error = sum(errors[:non_malicious_count])
            krumscore.append(current_error)
        aa = krumscore[0]
        krumscore[0] = krumscore[1]
        krumscore[1] = aa
        result = map(krumscore.index, heapq.nsmallest(non_malicious_count, krumscore))
        for i in result:
            selection_set.append(users_grads[i])

        current_grads = np.empty((users_grads.shape[1],), users_grads.dtype)
        users_gradsss = np.array(selection_set)
        for i, param_across_users in enumerate(users_gradsss.T):
            current_grads[i] = np.mean(param_across_users)

        current_weights = current_weights - self.params['lr'] * current_grads
        Helper.row_into_parameters(current_weights, target_model)
        return True

    import numpy as np

    def calculate_classwise_accuracy(self, cm):
        """
        Calculate class-wise accuracy from a confusion matrix.
        
        Arguments:
        - cm: Confusion matrix (2D numpy array or torch tensor).
        
        Returns:
        - class_accuracies: A dictionary with the class-wise accuracy for each class.
        """
        # Ensure the confusion matrix is a numpy array
        cm = cm.cpu().numpy() if isinstance(cm, torch.Tensor) else cm
        
        class_accuracies = {}
        num_classes = cm.shape[0]  # Number of classes
        
        for i in range(num_classes):
            true_positives = cm[i, i]  # Diagonal elements represent true positives for each class
            false_positives = cm[:, i].sum() - true_positives  # Sum of column - true positives
            false_negatives = cm[i, :].sum() - true_positives  # Sum of row - true positives
            true_negatives = cm.sum() - (true_positives + false_positives + false_negatives)
            
            # Accuracy for class 'i' is true positives / (true positives + false positives + false negatives)
            if true_positives + false_positives + false_negatives > 0:
                class_accuracy = true_positives / (true_positives + false_positives + false_negatives)
            else:
                class_accuracy = 0.0  # Handle the case where the denominator is zero
            
            class_accuracies[i] = class_accuracy

        return class_accuracies


    def enhanced_mkrum(self, target_model, updates, corrupted_count, users_count, validation_loader, validation_criterion,  threshold_ratio=0.15, small_weight=0.1, distances=None, return_index=False):
        
        # Step 1: Extract current model weights as a 1D numpy array
        current_weights = np.concatenate([param.data.cpu().numpy().flatten() for param in target_model.parameters()])
        
        # Step 2: Collect user gradients
        users_grads = np.empty((users_count, len(current_weights)), dtype=current_weights.dtype)
        users_grads = Helper.collect_gradients(updates, users_grads)
        logger.info(f"users count: {users_count} and corrupted count: {corrupted_count}.")
        
        # Step 3: Ensure there are enough non-malicious clients
        if not return_index:
            assert users_count >= 2 * corrupted_count + 1, (
                'users_count must be >= 2 * corrupted_count + 1', users_count, corrupted_count)
        
        non_malicious_count = users_count - corrupted_count
        selection_set = []
        krumscore = []
        
        # Step 4: Compute pairwise distances if not provided
        if distances is None:
            distances = Helper.krum_create_distances(users_grads)
        
        # Step 5: Compute Krum scores for each user
        for user in distances.keys():
            errors = sorted(distances[user].values())
            current_error = sum(errors[:non_malicious_count])
            krumscore.append(current_error)
        
        # Step 6: Select users with the smallest Krum scores
        selected_indices = heapq.nsmallest(users_count, range(len(krumscore)), key=lambda i: krumscore[i])
        logger.info(f"Selected all {users_count} users.")
        
        # Step 7: Initialize lists for suspicious and non-suspicious updates
        suspicious_updates = []
        non_suspicious_updates = []
        update_weights = [1.0] * users_count
        
        # Step 8: Backup the original model state
        original_model_state = copy.deepcopy(target_model.state_dict())
        logger.info("Original model state backed up.")

        ground_metrics_loss, ground_metrics_cm = self.validate_on_validation_set(target_model, validation_loader, validation_criterion)  # original model performance
        
        cm_list = []  # List to store confusion matrices
        susp_pair_clients = defaultdict(int)
        suspicious_count = 0
        logger.info(f"Confusion Matrix for target: \n{ground_metrics_cm}")

            # Calculate class-wise accuracy for this confusion matrix
        class_accuracies_g = self.calculate_classwise_accuracy(ground_metrics_cm)
        logger.info(f"Class-wise accuracy for target: {class_accuracies_g}")
        
        # Step 9: Iterate through each selected user's gradient for validation
        for idx in range(users_count):
            user_grad = users_grads[idx]
            
            # Restore the model to the original state using original_model_state
            target_model.load_state_dict(original_model_state)  # Restore model state
            
            # Apply the current user's gradient
            temp_weights = current_weights - self.params['lr'] * user_grad
            Helper.row_into_parameters(temp_weights, target_model)
            
            # Perform validation
            validation_loss, cm = self.validate_on_validation_set(target_model, validation_loader, validation_criterion)
            logger.info(f"Validation completed for user {idx}, confusion matrix appended.")

            # Log confusion matrix for each client
            logger.info(f"Confusion Matrix for user {idx}: \n{cm}")

            # Calculate class-wise accuracy for this confusion matrix
            class_accuracies = self.calculate_classwise_accuracy(cm)
            logger.info(f"Class-wise accuracy for user {idx}: {class_accuracies}")

            # cm_list.append(cm)  # Store confusion matrix for analysis

            classesMisclassified = defaultdict(list)

            # Update susp_pair_clients here in analyze_confusion_matrix
            is_suspicious = self.analyze_confusion_matrix(cm, ground_metrics_cm, susp_pair_clients, classesMisclassified, idx, non_suspicious_updates, suspicious_updates, update_weights)
            suspicious_count += is_suspicious
            if is_suspicious:
                logger.warning(f"Suspicious behavior detected for user {idx}.")
            else:
                logger.info(f"No suspicious behavior detected for user {idx}.")
        

        logger.info(f"Type of susp_pair_clients before calling assign_weights_based_on_misclassification: {type(susp_pair_clients)}")


        identified_adversaries = []

        # Step 10: Rank clients based on misclassification counts and assign weights
        update_weights, identified_adversaries = self.assign_weights_based_on_misclassification(susp_pair_clients, classesMisclassified ,update_weights, small_weight)

        
            
        # # Step 10: Analyze suspicious pairs across clients
        logger.info("Suspicious pairs analyzed across clients.")

        total_weight = sum(update_weights)  # Sum of all update weights (since update_weights is a list)
        weighted_grads = np.zeros_like(current_weights)

        # Loop through the selected clients and apply their updates
        for client_idx in selected_indices:
            weight = update_weights[client_idx]  # Get the corresponding weight for this client
            client_grad = users_grads[client_idx]  # Get the gradient of this client

            # Perform weighted aggregation of the client's update
            weighted_grads += (weight / total_weight) * client_grad

        # Update the central model with the aggregated, weighted gradients
        updated_weights = current_weights - self.params['lr'] * weighted_grads
        Helper.row_into_parameters(updated_weights, target_model)
        logger.info("Central model updated with weighted gradients.")



        # Step 10: Calculate and log adversarial client detection accuracy
        actual_adversaries = [0,1,2,3]
        top_n = 4  # Top 'n' clients to consider for accuracy calculation
        
        # Step 10.2: Calculate accuracy of adversarial detection
        correctly_identified = set(identified_adversaries).intersection(set(actual_adversaries))
        num_correctly_identified = len(correctly_identified)
        num_actual_adversaries = len(actual_adversaries)
        logger.info(f"Identified adversaries: {identified_adversaries}")
        logger.info(f"Actual adversaries: {actual_adversaries}")

        accuracy = num_correctly_identified / 4 if num_actual_adversaries > 0 else 0.0
        logger.info(f"Accuracy of identifying adversarial clients: {accuracy:.2%} "
                    f"({num_correctly_identified}/{top_n} correctly identified).")
        

    # Step 11: Return the updated model    
        
        if return_index:
            return True, suspicious_updates
        else:
            return True


    
    

    def validate_on_validation_set(self, model, validation_loader, criterion):
        model.eval()
        validation_loss = 0.0
        all_preds = []
        all_targets = []
        
        with torch.no_grad():
            for data, target in validation_loader:
                data, target = data.to(self.device), target.to(self.device)
                output = model(data)
                loss = criterion(output, target)
                validation_loss += loss.item()
                
                preds = output.argmax(dim=1).cpu().numpy()
                all_preds.extend(preds)
                all_targets.extend(target.cpu().numpy())
        
        cm = confusion_matrix(all_targets, all_preds, labels=list(range(100)))  # CIFAR-100 has 100 classes
        logger.info("Validation completed, confusion matrix created.")
        return validation_loss, cm




    # def assign_weights_based_on_misclassification(self, susp_pair_clients, classesMisclassified, update_weights, small_weight=0.1, top_n=4):
    #     # Confirm susp_pair_clients type at the start
    #     logger.info(f"susp_pair_clients type on entry to assign_weights_based_on_misclassification: {type(susp_pair_clients)}")

    #     # Assign to a temporary variable to prevent accidental overwriting
    #     susp_clients_dict = susp_pair_clients
    #     indetified_adversaries = []

    #     logger.info(f"susp_pair_clients type on susp_clients_dict = susp_pair_client: {type(susp_pair_clients)}")
        
    #     # Ensure susp_pair_clients is sorted by misclassification score
    #     sorted_clients = sorted(susp_clients_dict.items(), key=lambda x: x[1], reverse=True)

    #     # Step 2: Assign small weight to the top 'n' clients
    #     for i, (client_idx, score) in enumerate(sorted_clients):
    #         if i < top_n:
    #             update_weights[client_idx] = small_weight  # Assign small weight to top 'n' clients
    #             logger.info(f"Client {client_idx} ranked {i+1} with score {score}. Assigned small weight: {small_weight}")
    #             indetified_adversaries.append(client_idx)
    #         else:
    #             update_weights[client_idx] = 1.0  # Assign normal weight to other clients
    #             logger.info(f"Client {client_idx} assigned normal weight.")

    #     return update_weights,indetified_adversaries 

    # def assign_weights_based_on_misclassification(self, susp_pair_clients, classesMisclassified, update_weights, small_weight=0.1, top_n=4):
        
    #     identified_adversaries = []
    #     classesMisclassified = classesMisclassified
    #     # Step 1: Find the class with the maximum number of misclassifying clients
    #     max_class_id = max(classesMisclassified, key=lambda class_id: len(classesMisclassified[class_id]))

    #     for class_id in classesMisclassified.keys():
    #         if len(classesMisclassified[class_id]) == 4:
    #             client_list = classesMisclassified[class_id]


    #     # Step 2: Assign small weight to these clients
    #     if(client_list):
    #         for client in client_list:
    #             update_weights[client] = small_weight  # Assign small weight
    #             identified_adversaries.append(client)  # Track identified adversaries

    #         logger.info(f"Class {max_class_id} has the most misclassifying clients: {client_list}. "
    #                     f"Assigned small weight ({small_weight}) to these clients.")
            
    #     else:
    #         susp_clients_dict = susp_pair_clients
    #         logger.info(f"susp_pair_clients type on susp_clients_dict = susp_pair_client: {type(susp_pair_clients)}")
            
    #         # Ensure susp_pair_clients is sorted by misclassification score
    #         sorted_clients = sorted(susp_clients_dict.items(), key=lambda x: x[1], reverse=True)

    #         # Step 2: Assign small weight to the top 'n' clients
    #         for i, (client_idx, score) in enumerate(sorted_clients):
    #             if i < top_n:
    #                 update_weights[client_idx] = small_weight  # Assign small weight to top 'n' clients
    #                 logger.info(f"Client {client_idx} ranked {i+1} with score {score}. Assigned small weight: {small_weight}")
    #                 identified_adversaries.append(client_idx)
    #             else:
    #                 update_weights[client_idx] = 1.0  # Assign normal weight to other clients
    #                 logger.info(f"Client {client_idx} assigned normal weight.")

    #     return update_weights, identified_adversaries

    def assign_weights_based_on_misclassification(self, susp_pair_clients, classesMisclassified, update_weights, small_weight=0.1, top_n=4):
    
        susp_pair_clients=susp_pair_clients
        classesMisclassified=classesMisclassified
        identified_adversaries = []
        client_list = None  # Initialize client_list to None

        # Step 1: Find the class with exactly 4 misclassifying clients
        for class_id in classesMisclassified.keys():
            if len(classesMisclassified[class_id]) == 4:
                client_list = classesMisclassified[class_id]
                break  # Stop after finding the first matching class

        # Step 2: If a class with 4 misclassifying clients is found
        if client_list:
            for client in client_list:
                update_weights[client] = small_weight  # Assign small weight
                identified_adversaries.append(client)  # Track identified adversaries

            logger.info(f"Class with exactly 4 misclassifying clients found. "
                        f"Clients: {client_list}. Assigned small weight ({small_weight}) to these clients.")

        # Step 3: Fallback to susp_pair_clients if no class has 4 misclassifying clients
        else:
            logger.info("No class with exactly 4 misclassifying clients found. Falling back to susp_pair_clients.")
            logger.info(f"susp_pair_clients type: {type(susp_pair_clients)}")

            # Ensure susp_pair_clients is sorted by misclassification score
            sorted_clients = sorted(susp_pair_clients.items(), key=lambda x: x[1], reverse=True)

            # Assign small weight to the top 'n' suspicious clients
            for i, (client_idx, score) in enumerate(sorted_clients):
                if i < top_n:
                    update_weights[client_idx] = small_weight  # Assign small weight to top 'n' clients
                    logger.info(f"Client {client_idx} ranked {i+1} with score {score}. Assigned small weight: {small_weight}")
                    identified_adversaries.append(client_idx)
                else:
                    update_weights[client_idx] = 1.0  # Assign normal weight to other clients
                    logger.info(f"Client {client_idx} assigned normal weight.")

        return update_weights, identified_adversaries





    def analyze_confusion_matrix(self, cm, ground_metrics_cm, susp_pair_clients, classesMisclassified, client_idx, non_suspicious_updates, suspicious_updates, update_weights):
        threshold = 0.15  # Threshold for misclassification rate

        # Calculate class-wise accuracy for the ground truth confusion matrix
        target_accuracies = self.calculate_classwise_accuracy(ground_metrics_cm)
        client_accuracies = self.calculate_classwise_accuracy(cm)

        score = 0
        classesMisclassified = {}
        for class_id in target_accuracies.keys():
            target_acc = target_accuracies[class_id]
            client_acc = client_accuracies[class_id]
            
            # Calculate the absolute difference
            diff = target_acc - client_acc
            
            # Assign score if the difference is greater than the threshold
            if diff > threshold:
                if class_id not in classesMisclassified:
                    classesMisclassified[class_id] = []
                classesMisclassified[class_id].append(client_idx)
                score = score + 1
                logger.info(f"Class {class_id} has a difference of {diff:.2f} between target and client {client_idx}.")
            
        susp_pair_clients[client_idx] = score       

        if score > 0:  # For example, if more than one misclassification into the same target class
            suspicious_updates.append(client_idx)
            return True
        else:
            non_suspicious_updates.append(client_idx)
            return False



    def computekrum(self, users_grads, users_count, corrupted_count, distances=None, return_index=False, debug=False):
        if not return_index:
            assert users_count >= 2 * corrupted_count + 1, (
                'users_count>=2*corrupted_count + 3', users_count, corrupted_count)
        non_malicious_count = users_count - corrupted_count
        minimal_error = 1e20
        minimal_error_index = -1

        if distances is None:
            distances = self.krum_create_distances(users_grads)
        for user in distances.keys():
            errors = sorted(distances[user].values())
            current_error = sum(errors[:non_malicious_count])
            if current_error < minimal_error:
                minimal_error = current_error
                minimal_error_index = user

        if return_index:
            return minimal_error_index
        else:
            return users_grads[minimal_error_index]

    def computetrimmed(self, users_grads, users_count, corrupted_count):
        number_to_consider = int(users_grads.shape[0] - corrupted_count) - 1
        current_grads = np.empty((users_grads.shape[1],), users_grads.dtype)

        for i, param_across_users in enumerate(users_grads.T):
            med = np.median(param_across_users)
            good_vals = sorted(param_across_users - med, key=lambda x: abs(x))[:number_to_consider]
            current_grads[i] = np.mean(good_vals) + med
        return current_grads

    def bulyan(self, target_model, updates, users_count, corrupted_count):
        current_weights = np.concatenate([i.data.cpu().numpy().flatten() for i in target_model.parameters()])
        users_grads = np.empty((users_count, len(current_weights)), dtype=current_weights.dtype)
        users_grads = Helper.collect_gradients(updates, users_grads)

        assert users_count >= 4 * corrupted_count + 3
        set_size = users_count - 2 * corrupted_count
        selection_set = []

        distances = Helper.krum_create_distances(users_grads)
        while len(selection_set) < set_size:
            currently_selected = self.computekrum(users_grads, users_count - len(selection_set), corrupted_count,
                                                  distances, True)
            selection_set.append(users_grads[currently_selected])
            # remove the selected from next iterations:
            distances.pop(currently_selected)
            for remaining_user in distances.keys():
                distances[remaining_user].pop(currently_selected)

        current_grads = self.computetrimmed(np.array(selection_set), len(selection_set), 2 * 2)
        current_weights = current_weights - self.params['lr'] * current_grads
        Helper.row_into_parameters(current_weights, target_model)
        return True

    def median(self, target_model, users_count, updates):

        current_weights = np.concatenate([i.data.cpu().numpy().flatten() for i in target_model.parameters()])
        users_grads = np.empty((users_count, len(current_weights)), dtype=current_weights.dtype)
        users_grads = Helper.collect_gradients(updates, users_grads)

        current_grads = np.empty((users_grads.shape[1],), users_grads.dtype)
        for i, param_across_users in enumerate(users_grads.T):
            med = np.median(param_across_users)
            current_grads[i] = med


        current_weights = current_weights - self.params['lr'] * current_grads
        Helper.row_into_parameters(current_weights, target_model)
        return True

    def foolsgold_update(self, target_model, updates):
        client_grads = []
        alphas = []
        names = []
        for name, data in updates.items():
            client_grads.append(data[1])  # gradient
            alphas.append(data[0])  # num_samples
            names.append(name)

        adver_ratio = 0
        for i in range(0, len(names)):
            _name = names[i]
            if _name in self.params['adversary_list']:
                adver_ratio += alphas[i]
        adver_ratio = adver_ratio / sum(alphas)
        poison_fraction = adver_ratio * self.params['poisoning_per_batch'] / self.params['batch_size']
        logger.info(f'[foolsgold agg] training data poison_ratio: {adver_ratio}  data num: {alphas}')
        logger.info(f'[foolsgold agg] considering poison per batch poison_fraction: {poison_fraction}')

        target_model.train()
        # train and update
        optimizer = torch.optim.SGD(target_model.parameters(), lr=self.params['lr'],
                                    momentum=self.params['momentum'],
                                    weight_decay=self.params['decay'])

        optimizer.zero_grad()
        agg_grads, wv, alpha = self.fg.aggregate_gradients(client_grads, names)
        for i, (name, params) in enumerate(target_model.named_parameters()):
            agg_grads[i] = agg_grads[i] * self.params["eta"]
            if params.requires_grad:
                params.grad = agg_grads[i].to(config.device)
        optimizer.step()
        wv = wv.tolist()
        utils.csv_record.add_weight_result(names, wv, alpha)
        return True, names, wv, alpha

    def geometric_median_update(self, target_model, updates, maxiter=4, eps=1e-5, verbose=False, ftol=1e-6,
                                max_update_norm=None):
        """Computes geometric median of atoms with weights alphas using Weiszfeld's Algorithm
               """
        points = []
        alphas = []
        names = []
        for name, data in updates.items():
            points.append(data[1])  # update
            alphas.append(data[0])  # num_samples
            names.append(name)

        adver_ratio = 0
        for i in range(0, len(names)):
            _name = names[i]
            if _name in self.params['adversary_list']:
                adver_ratio += alphas[i]
        adver_ratio = adver_ratio / sum(alphas)
        poison_fraction = adver_ratio * self.params['poisoning_per_batch'] / self.params['batch_size']
        logger.info(f'[rfa agg] training data poison_ratio: {adver_ratio}  data num: {alphas}')
        logger.info(f'[rfa agg] considering poison per batch poison_fraction: {poison_fraction}')

        alphas = np.asarray(alphas, dtype=np.float64) / sum(alphas)
        alphas = torch.from_numpy(alphas).float()

        # alphas.float().to(config.device)
        median = Helper.weighted_average_oracle(points, alphas)
        num_oracle_calls = 1

        # logging
        obj_val = Helper.geometric_median_objective(median, points, alphas)
        logs = []
        log_entry = [0, obj_val, 0, 0]
        logs.append(log_entry)
        if verbose:
            logger.info('Starting Weiszfeld algorithm')
            logger.info(log_entry)
        logger.info(f'[rfa agg] init. name: {names}, weight: {alphas}')
        # start
        wv = None
        for i in range(maxiter):
            prev_median, prev_obj_val = median, obj_val
            weights = torch.tensor([alpha / max(eps, Helper.l2dist(median, p)) for alpha, p in zip(alphas, points)],
                                   dtype=alphas.dtype)
            weights = weights / weights.sum()
            median = Helper.weighted_average_oracle(points, weights)
            num_oracle_calls += 1
            obj_val = Helper.geometric_median_objective(median, points, alphas)
            log_entry = [i + 1, obj_val,
                         (prev_obj_val - obj_val) / obj_val,
                         Helper.l2dist(median, prev_median)]
            logs.append(log_entry)
            if verbose:
                logger.info(log_entry)
            if abs(prev_obj_val - obj_val) < ftol * obj_val:
                break
            logger.info(
                f'[rfa agg] iter:  {i}, prev_obj_val: {prev_obj_val}, obj_val: {obj_val}, abs dis: {abs(prev_obj_val - obj_val)}')
            logger.info(f'[rfa agg] iter:  {i}, weight: {weights}')
            wv = copy.deepcopy(weights)
        alphas = [Helper.l2dist(median, p) for p in points]

        update_norm = 0
        for name, data in median.items():
            update_norm += torch.sum(torch.pow(data, 2))
        update_norm = math.sqrt(update_norm)

        if max_update_norm is None or update_norm < max_update_norm:
            for name, data in target_model.state_dict().items():
                update_per_layer = median[name] * (self.params["eta"])
                if self.params['diff_privacy']:
                    update_per_layer.add_(self.dp_noise(data, self.params['sigma']))
                data.add_(update_per_layer)
            is_updated = True
        else:
            logger.info('\t\t\tUpdate norm = {} is too large. Update rejected'.format(update_norm))
            is_updated = False

        utils.csv_record.add_weight_result(names, wv.cpu().numpy().tolist(), alphas)

        return num_oracle_calls, is_updated, names, wv.cpu().numpy().tolist(), alphas


    @staticmethod
    def l2dist(p1, p2):
        """L2 distance between p1, p2, each of which is a list of nd-arrays"""
        squared_sum = 0
        for name, data in p1.items():
            squared_sum += torch.sum(torch.pow(p1[name] - p2[name], 2))
        return math.sqrt(squared_sum)

    @staticmethod
    def geometric_median_objective(median, points, alphas):
        """Compute geometric median objective."""
        temp_sum = 0
        for alpha, p in zip(alphas, points):
            temp_sum += alpha * Helper.l2dist(median, p)
        return temp_sum

        # return sum([alpha * Helper.l2dist(median, p) for alpha, p in zip(alphas, points)])

    @staticmethod
    def weighted_average_oracle(points, weights):
        """Computes weighted average of atoms with specified weights

        Args:
            points: list, whose weighted average we wish to calculate
                Each element is a list_of_np.ndarray
            weights: list of weights of the same length as atoms
        """
        tot_weights = torch.sum(weights)

        weighted_updates = dict()

        for name, data in points[0].items():
            weighted_updates[name] = torch.zeros_like(data)
        for w, p in zip(weights, points):  # 对每一个agent
            for name, data in weighted_updates.items():
                temp = (w / tot_weights).float().to(config.device)
                temp = temp * (p[name].float())
                # temp = w / tot_weights * p[name]
                if temp.dtype != data.dtype:
                    temp = temp.type_as(data)
                data.add_(temp)

        return weighted_updates

    def save_model(self, model=None, epoch=0, val_loss=0):
        if model is None:
            model = self.target_model
        if self.params['save_model']:
            # save_model
            logger.info("saving model")
            model_name = '{0}/model_last.pt.tar'.format(self.params['folder_path'])
            saved_dict = {'state_dict': model.state_dict(), 'epoch': epoch,
                          'lr': self.params['lr']}
            self.save_checkpoint(saved_dict, False, model_name)
            if epoch in self.params['save_on_epochs']:
                logger.info(f'Saving model on epoch {epoch}')
                self.save_checkpoint(saved_dict, False, filename=f'{model_name}.epoch_{epoch}')
            if val_loss < self.best_loss:
                self.save_checkpoint(saved_dict, False, f'{model_name}.best')
                self.best_loss = val_loss

    def update_epoch_submit_dict(self, epochs_submit_update_dict, global_epochs_submit_dict, epoch, state_keys):

        epoch_len = len(epochs_submit_update_dict[state_keys[0]])
        for j in range(0, epoch_len):
            per_epoch_dict = dict()
            for i in range(0, len(state_keys)):
                local_model_update_list = epochs_submit_update_dict[state_keys[i]]
                local_model_update_dict = local_model_update_list[j]
                per_epoch_dict[state_keys[i]] = local_model_update_dict

            global_epochs_submit_dict[epoch + j] = per_epoch_dict

        return global_epochs_submit_dict

    def save_epoch_submit_dict(self, global_epochs_submit_dict):
        with open(f'{self.folder_path}/epoch_submit_update.json', 'w') as outfile:
            json.dump(global_epochs_submit_dict, outfile, ensure_ascii=False, indent=1)

    def estimate_fisher(self, model, criterion,
                        data_loader, sample_size, batch_size=64):
        # sample loglikelihoods from the dataset.
        loglikelihoods = []
        if self.params['type'] == 'text':
            data_iterator = range(0, data_loader.size(0) - 1, self.params['bptt'])
            hidden = model.init_hidden(self.params['batch_size'])
        else:
            data_iterator = data_loader

        for batch_id, batch in enumerate(data_iterator):
            data, targets = self.get_batch(data_loader, batch,
                                           evaluation=False)
            if self.params['type'] == 'text':
                hidden = self.repackage_hidden(hidden)
                output, hidden = model(data, hidden)
                loss = criterion(output.view(-1, self.n_tokens), targets)
            else:
                output = model(data)
                loss = log_softmax(output, dim=1)[range(targets.shape[0]), targets.data]
                # loss = criterion(output.view(-1, ntokens
            # output, hidden = model(data, hidden)
            loglikelihoods.append(loss)
            # loglikelihoods.append(
            #     log_softmax(output.view(-1, self.n_tokens))[range(self.params['batch_size']), targets.data]
            # )

            # if len(loglikelihoods) >= sample_size // batch_size:
            #     break
        logger.info(loglikelihoods[0].shape)
        # estimate the fisher information of the parameters.
        loglikelihood = torch.cat(loglikelihoods).mean(0)
        logger.info(loglikelihood.shape)
        loglikelihood_grads = torch.autograd.grad(loglikelihood, model.parameters())

        parameter_names = [
            n.replace('.', '__') for n, p in model.named_parameters()
        ]
        return {n: g ** 2 for n, g in zip(parameter_names, loglikelihood_grads)}

    def consolidate(self, model, fisher):
        for n, p in model.named_parameters():
            n = n.replace('.', '__')
            model.register_buffer('{}_estimated_mean'.format(n), p.data.clone())
            model.register_buffer('{}_estimated_fisher'
                                  .format(n), fisher[n].data.clone())

    def ewc_loss(self, model, lamda, cuda=False):
        try:
            losses = []
            for n, p in model.named_parameters():
                # retrieve the consolidated mean and fisher information.
                n = n.replace('.', '__')
                mean = getattr(model, '{}_estimated_mean'.format(n))
                fisher = getattr(model, '{}_estimated_fisher'.format(n))
                # wrap mean and fisher in variables.
                mean = Variable(mean)
                fisher = Variable(fisher)
                # calculate a ewc loss. (assumes the parameter's prior as
                # gaussian distribution with the estimated mean and the
                # estimated cramer-rao lower bound variance, which is
                # equivalent to the inverse of fisher information)
                losses.append((fisher * (p - mean) ** 2).sum())
            return (lamda / 2) * sum(losses)
        except AttributeError:
            # ewc loss is 0 if there's no consolidated parameters.
            return (
                Variable(torch.zeros(1)).cuda() if cuda else
                Variable(torch.zeros(1))
            )


class FoolsGold(object):
    def __init__(self, use_memory=False):
        self.memory = None
        self.memory_dict = dict()
        self.wv_history = []
        self.use_memory = use_memory

    def aggregate_gradients(self, client_grads, names):
        cur_time = time.time()
        num_clients = len(client_grads)
        grad_len = np.array(client_grads[0][-2].cpu().data.numpy().shape).prod()

        # if self.memory is None:
        #     self.memory = np.zeros((num_clients, grad_len))
        self.memory = np.zeros((num_clients, grad_len))
        grads = np.zeros((num_clients, grad_len))
        for i in range(len(client_grads)):
            grads[i] = np.reshape(client_grads[i][-2].cpu().data.numpy(), (grad_len))
            if names[i] in self.memory_dict.keys():
                self.memory_dict[names[i]] += grads[i]
            else:
                self.memory_dict[names[i]] = copy.deepcopy(grads[i])
            self.memory[i] = self.memory_dict[names[i]]
        # self.memory += grads

        if self.use_memory:
            wv, alpha = self.foolsgold(self.memory)  # Use FG
        else:
            wv, alpha = self.foolsgold(grads)  # Use FG
        logger.info(f'[foolsgold agg] wv: {wv}')
        self.wv_history.append(wv)

        agg_grads = []
        # Iterate through each layer
        for i in range(len(client_grads[0])):
            assert len(wv) == len(client_grads), 'len of wv {} is not consistent with len of client_grads {}'.format(
                len(wv), len(client_grads))
            temp = wv[0] * client_grads[0][i].cpu().clone()
            # Aggregate gradients for a layer
            for c, client_grad in enumerate(client_grads):
                if c == 0:
                    continue
                temp += wv[c] * client_grad[i].cpu()
            temp = temp / len(client_grads)
            agg_grads.append(temp)
        print('model aggregation took {}s'.format(time.time() - cur_time))
        return agg_grads, wv, alpha

    def foolsgold(self, grads):
        """
        :param grads:
        :return: compute similatiry and return weightings
        """
        n_clients = grads.shape[0]
        cs = smp.cosine_similarity(grads) - np.eye(n_clients)

        maxcs = np.max(cs, axis=1)
        # pardoning
        for i in range(n_clients):
            for j in range(n_clients):
                if i == j:
                    continue
                if maxcs[i] < maxcs[j]:
                    cs[i][j] = cs[i][j] * maxcs[i] / maxcs[j]
        wv = 1 - (np.max(cs, axis=1))

        wv[wv > 1] = 1
        wv[wv < 0] = 0

        alpha = np.max(cs, axis=1)

        # Rescale so that max value is wv
        wv = wv / np.max(wv)
        wv[(wv == 1)] = .99

        # Logit function
        wv = (np.log(wv / (1 - wv)) + 0.5)
        wv[(np.isinf(wv) + wv > 1)] = 1
        wv[(wv < 0)] = 0

        # wv is the weight
        return wv, alpha
