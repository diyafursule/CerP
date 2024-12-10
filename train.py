import cifar100_train
import config


def train(helper, start_epoch, local_model, target_model, is_poison, agent_name_keys, noise_trigger, intinal_trigger):
    epochs_submit_update_dict = {}
    num_samples_dict = {}
    user_grad = []
    server_update = dict()
    if helper.params['type'] == config.TYPE_CIFAR100:
        epochs_submit_update_dict, num_samples_dict, user_grad, server_update, tuned_trigger = cifar100_train.Cifar100Train(
            helper,
            start_epoch,
            local_model,
            target_model,
            is_poison,
            agent_name_keys,
            noise_trigger,
            intinal_trigger)
    return epochs_submit_update_dict, num_samples_dict, user_grad, server_update, tuned_trigger


# import cifar100_train
# import config
# import logging

# def train(helper, start_epoch, local_model, target_model, is_poison, agent_name_keys, noise_trigger, intinal_trigger):
#     """
#     Trains the model with the specified parameters.

#     Parameters:
#     - helper: The helper object containing various configurations and utilities.
#     - start_epoch: The epoch from which training should start.
#     - local_model: The local model to be trained.
#     - target_model: The target model to be updated.
#     - is_poison: Boolean indicating whether poisoning is used.
#     - agent_name_keys: List of agent names for training.
#     - noise_trigger: The trigger used for poisoning.
#     - intinal_trigger: The initial trigger pattern.

#     Returns:
#     - epochs_submit_update_dict: Dictionary containing the updates for each epoch.
#     - num_samples_dict: Dictionary containing the number of samples per agent.
#     - user_grad: List of user gradients.
#     - server_update: Dictionary of server updates.
#     - tuned_trigger: The tuned trigger pattern.
#     """
#     try:
#         epochs_submit_update_dict = {}
#         num_samples_dict = {}
#         user_grad = []
#         server_update = dict()

#         if helper.params['type'] == config.TYPE_CIFAR100:
#             (epochs_submit_update_dict, num_samples_dict, user_grad,
#              server_update, tuned_trigger) = cifar100_train.Cifar100Train(
#                 helper,
#                 start_epoch,
#                 local_model,
#                 target_model,
#                 is_poison,
#                 agent_name_keys,
#                 noise_trigger,
#                 intinal_trigger)
#         else:
#             logging.warning(f"Unsupported type: {helper.params['type']}")
#             # Handle unsupported types or return default values if necessary

#         return epochs_submit_update_dict, num_samples_dict, user_grad, server_update, tuned_trigger
    
#     except Exception as e:
#         logging.error(f"An error occurred during training: {e}")
#         # Optionally, handle the exception or re-raise
#         raise
