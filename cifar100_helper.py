from collections import defaultdict
import matplotlib.pyplot as plt
import torch
import torch.utils.data
from helper import Helper
import random
import logging
from torchvision import datasets, transforms
import numpy as np
from model.resnet_cifar100 import ResNet18100
from torch.utils.data import random_split



logger = logging.getLogger("logger")
import config
from config import device
import copy
import yaml
import os

os.environ['KMP_DUPLICATE_LIB_OK'] = 'True'
import datetime
import json


class Cifar100Helper(Helper):

    def create_model(self):
        local_model = None
        target_model = None
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

        if self.params['type'] == config.TYPE_CIFAR100:
            local_model = ResNet18100(name='Local',
                                      created_time=self.params['current_time'])
            target_model = ResNet18100(name='Target',
                                       created_time=self.params['current_time'])

        local_model = local_model.to(device)
        target_model = target_model.to(device)
        if self.params['resumed_model']:
            if torch.cuda.is_available():
                loaded_params = torch.load(f"saved_models/{self.params['resumed_model_name']}", map_location=device)
            else:
                loaded_params = torch.load(f"saved_models/{self.params['resumed_model_name']}", map_location='cpu')
            target_model.load_state_dict(loaded_params['state_dict'])
            self.start_epoch = loaded_params['epoch'] + 1
            self.params['lr'] = loaded_params.get('lr', self.params['lr'])
            logger.info(f"Loaded parameters from saved model: LR is"
                        f" {self.params['lr']} and current epoch is {self.start_epoch}")
        else:
            self.start_epoch = 1

        self.local_model = local_model
        self.target_model = target_model

    def build_classes_dict(self):
        cifar_classes = {}
        for ind, x in enumerate(self.train_dataset):
            _, label = x
            if label in cifar_classes:
                cifar_classes[label].append(ind)
            else:
                cifar_classes[label] = [ind]
        return cifar_classes


    def sample_dirichlet_train_data(self, no_participants, alpha=0.9):
        """
            Input: Number of participants and alpha (param for distribution)
            Output: A list of indices denoting data in CIFAR training set.数据索引列表
            Requires: cifar_classes, a preprocessed class-indice dictionary.
            Sample Method: take a uniformly sampled 10-dimension vector as parameters for
            dirichlet distribution to sample number of images in each class.#每一维代表着每一个类别里抽取多少数据
        """

        cifar_classes = self.classes_dict
        class_size = len(cifar_classes[0])
        per_participant_list = defaultdict(list)
        no_classes = len(cifar_classes.keys())

        image_nums = []
        scalenum = 10
        for n in range(no_classes):
            image_num = []
            random.shuffle(cifar_classes[n])
            sampled_probabilities = int(scalenum) * class_size * np.random.dirichlet(
                np.array(no_participants * [alpha]))
            for user in range(no_participants):
                no_imgs = int(round(sampled_probabilities[user]))
                sampled_list = cifar_classes[n][:min(len(cifar_classes[n]), no_imgs)]
                image_num.append(len(sampled_list))
                per_participant_list[user].extend(sampled_list)
                random.shuffle(cifar_classes[n])
            image_nums.append(image_num)

        return per_participant_list

    def draw_dirichlet_plot(self, no_classes, no_participants, image_nums, alpha):
        fig = plt.figure(figsize=(10, 5))
        s = np.empty([no_classes, no_participants])
        for i in range(0, len(image_nums)):
            for j in range(0, len(image_nums[0])):
                s[i][j] = image_nums[i][j]
        s = s.transpose()
        left = 0
        y_labels = []
        category_colors = plt.get_cmap('RdYlGn')(
            np.linspace(0.15, 0.85, no_participants))
        for k in range(no_classes):
            y_labels.append('Label ' + str(k))
        vis_par = [0, 10, 20, 30]
        for k in range(no_participants):
            # for k in vis_par:
            color = category_colors[k]
            plt.barh(y_labels, s[k], left=left, label=str(k), color=color)
            widths = s[k]
            xcenters = left + widths / 2
            r, g, b, _ = color
            text_color = 'white' if r * g * b < 0.5 else 'darkgrey'
            # for y, (x, c) in enumerate(zip(xcenters, widths)):
            #     plt.text(x, y, str(int(c)), ha='center', va='center',
            #              color=text_color,fontsize='small')
            left += s[k]
        plt.legend(ncol=20, loc='lower left', bbox_to_anchor=(0, 1), fontsize=4)  #
        # plt.legend(ncol=len(vis_par), bbox_to_anchor=(0, 1),
        #            loc='lower left', fontsize='small')
        plt.xlabel("Number of Images", fontsize=16)
        # plt.ylabel("Label 0 ~ 199", fontsize=16)
        # plt.yticks([])
        fig.tight_layout(pad=0.1)
        # plt.ylabel("Label",fontsize='small')
        fig.savefig(self.folder_path + '/Num_Img_Dirichlet_Alpha{}.pdf'.format(alpha))

    def poison_test_dataset(self):
        logger.info('get poison test loader')
        # delete the test data with target label
        test_classes = {}
        for ind, x in enumerate(self.test_dataset):
            _, label = x
            if label in test_classes:
                test_classes[label].append(ind)
            else:
                test_classes[label] = [ind]

        range_no_id = list(range(0, len(self.test_dataset)))
        for image_ind in test_classes[self.params['poison_label_swap']]:
            if image_ind in range_no_id:
                range_no_id.remove(image_ind)
        poison_label_inds = test_classes[self.params['poison_label_swap']]

        return torch.utils.data.DataLoader(self.test_dataset,
                                           batch_size=self.params['batch_size'],
                                           sampler=torch.utils.data.sampler.SubsetRandomSampler(
                                               range_no_id)), \
               torch.utils.data.DataLoader(self.test_dataset,
                                           batch_size=self.params['batch_size'],
                                           sampler=torch.utils.data.sampler.SubsetRandomSampler(
                                               poison_label_inds))
    
    # def load_data(self):
    #     logger.info('Loading data')
    #     dataPath = './data'

    #     if self.params['type'] == config.TYPE_CIFAR100:
    #         transform_train = transforms.Compose([
    #             transforms.RandomCrop(32, padding=4),
    #             transforms.RandomHorizontalFlip(),
    #             transforms.ToTensor(),
    #             transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010))
    #         ])

    #         transform_test = transforms.Compose([
    #             transforms.ToTensor(),
    #             transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010))
    #         ])

    #         self.train_dataset = datasets.CIFAR100(dataPath, train=True, download=True,
    #                                                transform=transform_train)

    #         self.test_dataset = datasets.CIFAR100(dataPath, train=False, transform=transform_test)

    #     self.classes_dict = self.build_classes_dict()
    #     logger.info('build_classes_dict done')

    #     # Split the training dataset into training and validation datasets
    #     val_size = int(0.1 * len(self.train_dataset))  # 10% for validation
    #     train_size = len(self.train_dataset) - val_size
    #     self.train_dataset, self.val_dataset = torch.utils.data.random_split(self.train_dataset, [train_size, val_size])


    #     logger.info('train and validation datasets created')

    #     if self.params['sampling_dirichlet']:
    #         indices_per_participant = self.sample_dirichlet_train_data(
    #             self.params['number_of_total_participants'],  # 100
    #             alpha=self.params['dirichlet_alpha'])
    #         train_loaders = [(pos, self.get_train(indices)) for pos, indices in
    #                          indices_per_participant.items()]
    #     else:
    #         all_range = list(range(len(self.train_dataset)))
    #         random.shuffle(all_range)
    #         train_loaders = [(pos, self.get_train_old(all_range, pos))
    #                          for pos in range(self.params['number_of_total_participants'])]

    #     logger.info('train loaders done')
    #     self.train_data = train_loaders
    #     logger.info(f"Total participants: {len(self.train_data)}")
    #     self.test_data = self.get_test()
    #     logger.info(f"Total participants(test data): {len(self.test_data)}")
    #     self.test_data_poison, self.test_targetlabel_data = self.poison_test_dataset()
    #     if len(self.train_data) == 0:
    #         logger.error("Train dataset is empty after loading.")
    #         raise ValueError("Train dataset is empty!")


    #     self.advasarial_namelist = self.params['adversary_list']

    #     if not self.params['is_random_namelist']:
    #         self.participants_list = self.params['participants_namelist']
    #     else:
    #         self.participants_list = list(range(self.params['number_of_total_participants']))

    #     self.benign_namelist = list(set(self.participants_list) - set(self.advasarial_namelist))

    #     # Create validation DataLoader
    #     self.val_loader = torch.utils.data.DataLoader(self.val_dataset,
    #                                                   batch_size=self.params['batch_size'],
    #                                                   shuffle=False)

    #     logger.info('Validation loader created')

    # def get_val(self):
    #     return self.val_loader



    def load_data(self):
        logger.info('Loading data')
        dataPath = './data'

        if self.params['type'] == config.TYPE_CIFAR100:
            ### data load
            transform_train = transforms.Compose([
                transforms.RandomCrop(32, padding=4),
                transforms.RandomHorizontalFlip(),
                transforms.ToTensor(),
                transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010))
            ])

            transform_test = transforms.Compose([
                transforms.ToTensor(),
                transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010))
            ])

            # Load full training set and split it into training and validation
            full_train_dataset = datasets.CIFAR100(dataPath, train=True, download=True, transform=transform_train)

            val_size = int(0.1 * len(full_train_dataset))  # Use 10% of the training data for validation
            train_size = len(full_train_dataset) - val_size

            self.train_dataset, self.val_dataset = random_split(full_train_dataset, [train_size, val_size])

            self.test_dataset = datasets.CIFAR100(dataPath, train=False, transform=transform_test)

        self.classes_dict = self.build_classes_dict()
        logger.info('build_classes_dict done')

        if self.params['sampling_dirichlet']:
            ## sample indices for participants using Dirichlet distribution
            indices_per_participant = self.sample_dirichlet_train_data(
                self.params['number_of_total_participants'],
                alpha=self.params['dirichlet_alpha']
            )
            train_loaders = [(pos, self.get_train(indices)) for pos, indices in indices_per_participant.items()]
        else:
            ## sample indices for participants equally
            all_range = list(range(len(self.train_dataset)))
            random.shuffle(all_range)
            train_loaders = [(pos, self.get_train_old(all_range, pos)) for pos in range(self.params['number_of_total_participants'])]

        logger.info('train loaders done')

        self.train_data = train_loaders
        self.test_data = self.get_test()
        self.test_data_poison, self.test_targetlabel_data = self.poison_test_dataset()
        self.val_data = self.get_val()  # New validation loader

        self.advasarial_namelist = self.params['adversary_list']

        if self.params['is_random_namelist'] == False:
            self.participants_list = self.params['participants_namelist']
        else:
            self.participants_list = list(range(self.params['number_of_total_participants']))

        self.benign_namelist = list(set(self.participants_list) - set(self.advasarial_namelist))

    def get_val(self):
        # Create DataLoader for validation set
        val_loader = torch.utils.data.DataLoader(self.val_dataset,
                                                 batch_size=self.params['batch_size'],
                                                 shuffle=False, num_workers=8, pin_memory=True)
        return val_loader

    
    
    def get_train(self, indices):
        """
        This method is used along with Dirichlet distribution
        :param params:
        :param indices:
        :return:
        """
        if len(self.train_dataset) == 0:
            logger.info("Train dataset is empty!")
            raise ValueError("Train dataset is empty!")
        
        

        train_loader = torch.utils.data.DataLoader(self.train_dataset,
                                                   batch_size=self.params['batch_size'],
                                                   sampler=torch.utils.data.sampler.SubsetRandomSampler(
                                                       indices), pin_memory=True, num_workers=8)
        return train_loader

    def get_train_old(self, all_range, model_no):
        """
        This method equally splits the dataset.
        :param params:
        :param all_range:
        :param model_no:
        :return:
        """

        data_len = int(len(self.train_dataset) / self.params['number_of_total_participants'])
        sub_indices = all_range[model_no * data_len: (model_no + 1) * data_len]
        train_loader = torch.utils.data.DataLoader(self.train_dataset,
                                                   batch_size=self.params['batch_size'],
                                                   sampler=torch.utils.data.sampler.SubsetRandomSampler(
                                                       sub_indices))
        return train_loader

    def get_test(self):
        if len(self.test_dataset) == 0:
            logger.info("Test dataset is empty!")
            raise ValueError("Test dataset is empty!")
        test_loader = torch.utils.data.DataLoader(self.test_dataset,
                                                  batch_size=self.params['test_batch_size'],
                                                  shuffle=False)
        return test_loader
 


    def get_batch(self, train_data, bptt, evaluation=False):
        data, target = bptt
        data = data.to(device)
        target = target.to(device)
        if evaluation:
            data.requires_grad_(False)
            target.requires_grad_(False)
        return data, target

    def get_poison_batch(self, bptt, noise_trigger, adversarial_index=-1, evaluation=False):

        images, targets = bptt

        poison_count = 0
        new_images = images
        new_targets = targets


        for index in range(0, len(images)):
            if evaluation:  # poison all data when testing
                new_targets[index] = self.params['poison_label_swap']
                new_images[index] = self.add_pixel_pattern(images[index], noise_trigger)
                poison_count += 1

            else:  # poison part of data when training
                if index < self.params['poisoning_per_batch']:
                    new_targets[index] = self.params['poison_label_swap']
                    new_images[index] = self.add_pixel_pattern(images[index], noise_trigger)
                    poison_count += 1
                else:
                    new_images[index] = images[index]
                    new_targets[index] = targets[index]

        new_images = new_images.to(device)
        new_targets = new_targets.to(device).long()
        if evaluation:
            new_images.requires_grad_(False)
            new_targets.requires_grad_(False)
        return new_images, new_targets, poison_count

    def add_pixel_pattern(self, ori_image, noise_trigger):
        image = copy.deepcopy(ori_image)
        noise = noise_trigger.clone().detach().cpu()
        poison_patterns = []
        for i in range(0, self.params['trigger_num']):
            poison_patterns = poison_patterns + self.params[str(i) + '_poison_pattern']
        for i in range(0, len(poison_patterns)):
            pos = poison_patterns[i]
            image[0][pos[0]][pos[1]] = noise[0][pos[0]][pos[1]]
            image[1][pos[0]][pos[1]] = noise[1][pos[0]][pos[1]]
            image[2][pos[0]][pos[1]] = noise[2][pos[0]][pos[1]]

        image = torch.clamp(image, -1, 1)

        return image


if __name__ == '__main__':
    np.random.seed(1)
    with open(f'./utils/cifar_params.yaml', 'r') as f:
        params_loaded = yaml.load(f)
    current_time = datetime.datetime.now().strftime('%b.%d_%H.%M.%S')
    helper = Cifar100Helper(current_time=current_time, params=params_loaded,
                            name=params_loaded.get('name', 'mnist'))
    helper.load_data()

    if len(helper.train_data) == 0:
            logger.error("Train dataset is empty after loading.")
            raise ValueError("Train dataset is empty!")


    pars = list(range(100))
    # show the data distribution among all participants.
    count_all = 0
    for par in pars:
        cifar_class_count = dict()
        for i in range(10):
            cifar_class_count[i] = 0
        count = 0
        _, data_iterator = helper.train_data[par]
        for batch_id, batch in enumerate(data_iterator):
            data, targets = batch
            for t in targets:
                cifar_class_count[t.item()] += 1
            count += len(targets)
        count_all += count
        print(par, cifar_class_count, count, max(zip(cifar_class_count.values(), cifar_class_count.keys())))

    print('avg', count_all * 1.0 / 100)
