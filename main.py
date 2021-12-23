# This is a sample Python script.

# Press Shift+F10 to execute it or replace it with your code.
# Press Double Shift to search everywhere for classes, files, tool windows, actions, and settings.


def print_hi(name):
    # Use a breakpoint in the code line below to debug your script.
    print(f'Hi, {name}')  # Press Ctrl+F8 to toggle the breakpoint.


# Press the green button in the gutter to run the script.
if __name__ == '__main__':

    print_hi('PyCharm')
    import numpy as np  # linear algebra
    import pandas as pd  # data processing, CSV file I/O (e.g. pd.read_csv)

    # Input data files are available in the read-only "../input/" directory
    # For example, running this (by clicking run or pressing Shift+Enter) will list all files under the input directory

    import os

    for dirname, _, filenames in os.walk('kaggle/input'):
        for filename in filenames:
            print(os.path.join(dirname, filename))

    import cv2
    import os
    import re
    import torch
    import torchvision
    from torchvision import transforms
    from torchvision.models.detection.faster_rcnn import FastRCNNPredictor
    from torchvision.models.detection import FasterRCNN
    from torchvision.models.detection.rpn import AnchorGenerator
    from torch.utils.data import DataLoader, Dataset
    from torch.utils.data.sampler import SequentialSampler
    from matplotlib import pyplot as plt
    import logging

    logging.basicConfig(filename=os.path.join(os.getcwd(), 'log.txt'), level=logging.INFO)

    train_df = pd.read_csv("kaggle/input/global-wheat-detection/train.csv")
    submit = pd.read_csv("kaggle/input/global-wheat-detection/sample_submission.csv")

    train_df.head()
    train_df = train_df.drop(columns=['width', 'height', 'source'])  # 这三个属性可以不需要 我们只需要看成二分类问题 背景和小麦
    train_df['x'] = -1
    train_df['y'] = -1
    train_df['w'] = -1
    train_df['h'] = -1

    WEIGHTS_FILE = 'trained24.pth'  # 预训练模型的位置 'kaggle/input/fasterrcnn/fasterrcnn_resnet50_fpn_best.pth'


    # 将[x,y,w,h]数据格式的锚框提取出来 作为dataFrame的x,y,w,h列 这是一个中间函数
    def expand_bbox(x):
        r = np.array(re.findall("([0-9]+[.]?[0-9]*)", x))
        if len(r) == 0:
            r = [-1, -1, -1, -1]
        return r


    # dataFrame提取 bbox:[x,y,w,h] -> x:x,y:y,w:w,h:h.
    train_df[['x', 'y', 'w', 'h']] = np.stack(
        train_df['bbox'].apply(lambda x: expand_bbox(x)))

    train_df['x'] = train_df['x'].astype(np.float)  # 转成float类型
    train_df['y'] = train_df['y'].astype(np.float)
    train_df['w'] = train_df['w'].astype(np.float)
    train_df['h'] = train_df['h'].astype(np.float)

    image_ids = train_df['image_id'].unique()  # 取出倒数665个做验证集，前面的做训练集
    valid_ids = image_ids[-665:]
    train_ids = image_ids[:-665]

    # 重新调整训练集和验证集的分布的dataFrame
    valid_df = train_df[train_df['image_id'].isin(valid_ids)]
    train_df = train_df[train_df['image_id'].isin(train_ids)]

    trans = transforms.Compose([transforms.ToTensor()])  # 转成tensor的变换函数


    # 处理数据集
    class WheatDataset(Dataset):
        # 参数 数据、图片地址、转换函数、是否为训练和验证集
        def __init__(self, dataframe, image_dir, transforms=None, train=True):
            super().__init__()
            # 取出所有图片id
            self.image_ids = dataframe['image_id'].unique()
            self.df = dataframe
            self.df = dataframe
            self.image_dir = image_dir
            self.transforms = transforms
            self.train = train

        def __len__(self) -> int:
            return self.image_ids.shape[0]

        def __getitem__(self, index: int):

            image_id = self.image_ids[index]
            image = cv2.imread(f'{self.image_dir}/{image_id}.jpg', cv2.IMREAD_COLOR)
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB).astype(np.float32)
            image /= 255.0
            if self.transforms is not None:  # 训练和验证时转为tensor
                image = self.transforms(image)
            if (self.train == False):  # 对于 测试集，直接返回即可
                return image, image_id

            records = self.df[self.df['image_id'] == image_id]
            # 将boxes从[x,y,w,h]转换成[x1,y1,x2,y2]的标准格式
            boxes = records[['x', 'y', 'w', 'h']].values
            boxes[:, 2] = boxes[:, 0] + boxes[:, 2]
            boxes[:, 3] = boxes[:, 1] + boxes[:, 3]
            boxes = torch.as_tensor(boxes, dtype=torch.float32)
            # 计算面积
            area = (boxes[:, 3] - boxes[:, 1]) * (boxes[:, 2] - boxes[:, 0])
            area = torch.as_tensor(area, dtype=torch.float32)
            # 只有一个类别，即小麦 所以默认取1
            labels = torch.ones((records.shape[0]), dtype=torch.int64)
            # 背景取0
            iscrowd = torch.zeros((records.shape[0]), dtype=torch.int64)

            target = {}
            # 锚框
            target['boxes'] = boxes
            # 标注
            target['labels'] = labels
            target['image_id'] = torch.tensor([index])
            target['area'] = area
            target['iscrowd'] = iscrowd
            # images(3,1024,1024)
            # targets dict: {
            #   boxes :[num_of_box,4],  labels :[num_of_box],   image_id:[1], area:[47], iscrowd:[47]
            # }

            return image, target, image_id


    train_dir = 'kaggle/input/global-wheat-detection/train'
    test_dir = 'kaggle/input/global-wheat-detection/test'


    class Averager:  ##Return the average loss
        def __init__(self):
            self.current_total = 0.0
            self.iterations = 0.0

        def send(self, value):
            self.current_total += value
            self.iterations += 1

        @property
        def value(self):
            if self.iterations == 0:
                return 0
            else:
                return 1.0 * self.current_total / self.iterations

        def reset(self):
            self.current_total = 0.0
            self.iterations = 0.0


    def collate_fn(batch):
        return tuple(zip(*batch))


    train_dataset = WheatDataset(train_df, train_dir, trans, True)
    valid_dataset = WheatDataset(valid_df, train_dir, trans, True)

    # data loader
    train_data_loader = DataLoader(
        train_dataset,
        batch_size=9,
        shuffle=True,
        num_workers=0,
        collate_fn=collate_fn
    )

    valid_data_loader = DataLoader(
        valid_dataset,
        batch_size=9,
        shuffle=True,
        num_workers=0,
        collate_fn=collate_fn
    )
    device = torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu')

    # 绘制预览图片
    images, targets, image_ids = next(iter(train_data_loader))

    images = list(image.to(device) for image in images)
    targets = [{k: v.to(device) for k, v in t.items()} for t in targets]
    for image, target in zip(images, targets):
        boxes = target['boxes'].cpu().numpy().astype(np.int32)
        sample = image.permute(1, 2, 0).cpu().numpy().copy()

        fig, ax = plt.subplots(1, 1, figsize=(16, 8))

        for box in boxes:
            cv2.rectangle(sample,
                        (box[0], box[1]),
                        (box[2], box[3]),
                        (220, 0, 0), 3)
        ax.set_axis_off()
        ax.imshow(sample)
        plt.show()



    # 从torchvision中取出fasterrcnn_resnet50_fpn模型
    model = torchvision.models.detection.fasterrcnn_resnet50_fpn(pretrained=False, pretrained_backbone=False)

    # 输入特征
    in_features = model.roi_heads.box_predictor.cls_score.in_features

    #  pre-trained head 替换
    model.roi_heads.box_predictor = FastRCNNPredictor(in_features, num_classes=2)

    # 载入coco数据集预训练的模型
    model.load_state_dict(torch.load(WEIGHTS_FILE))  ##Load pre trained weights

    model.train()
    model.to(device)
    params = [p for p in model.parameters() if p.requires_grad]
    # 优化器 调参重点
    optimizer = torch.optim.SGD(params, lr=0.001, momentum=0.75, weight_decay=0.00001)
    # torch.optim.Adam(model.parameters(),lr=0.00001,  betas=(0.9, 0.999), eps=1e-08, weight_decay=0)
    # torch.optim.SGD(params, lr=0.01, momentum=0.9, weight_decay=0.00001)
    # torch.optim.Adam(model.parameters(), lr=0.005,  betas=(0.9, 0.999), eps=1e-08, weight_decay=0.00001)
    lr_scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=1, gamma=0.5)
    # lr_scheduler = None

    num_epochs = 1

    loss_hist = Averager()
    itr = 1

    for epoch in range(num_epochs):
        loss_hist.reset()

        for images, targets, image_ids in train_data_loader:

            images = list(image.to(device) for image in images)
            targets = [{k: v.to(device) for k, v in t.items()} for t in targets]

            loss_dict = model(images, targets)  ##Return the loss

            losses = sum(loss for loss in loss_dict.values())
            loss_value = losses.item()

            loss_hist.send(loss_value)  # Average out the loss

            optimizer.zero_grad()
            losses.backward()
            optimizer.step()

            if itr % 50 == 0:
                logging.info(f"Iteration #{itr} loss: {loss_value}")
                print(f"Iteration #{itr} loss: {loss_value}")

            itr += 1

        # update the learning rate
        if lr_scheduler is not None:
            lr_scheduler.step()
        torch.save(model.state_dict(), 'trained' + str(epoch + 60) + '.pth')
        logging.info(f"Epoch #{epoch} loss: {loss_hist.value}")
        print(f"Epoch #{epoch} loss: {loss_hist.value}")

    test_dataset = WheatDataset(submit, test_dir, trans, False)
    test_data_loader = DataLoader(test_dataset, batch_size=1, shuffle=True)  ##Test dataloader

    detection_threshold = 0.4


    def format_prediction_string(boxes, scores):  ## Define the formate for storing prediction results
        pred_strings = []
        for j in zip(scores, boxes):
            pred_strings.append("{0:.4f} {1} {2} {3} {4}".format(j[0], j[1][0], j[1][1], j[1][2], j[1][3]))

        return " ".join(pred_strings)


    ## Lets make the prediction
    results = []
    model.eval()
    outs = []
    for images, image_ids in test_data_loader:

        images = list(image.to(device) for image in images)
        outputs = model(images)

        for i, image in enumerate(images):
            boxes = outputs[i]['boxes'].data.cpu().numpy()  ##Formate of the output's box is [Xmin,Ymin,Xmax,Ymax]
            scores = outputs[i]['scores'].data.cpu().numpy()

            boxes = boxes[scores >= detection_threshold].astype(
                np.int32)  # Compare the score of output with the threshold and
            scores = scores[scores >= detection_threshold]  # slelect only those boxes whose score is greater
            # than threshold value
            image_id = image_ids[i]

            boxes[:, 2] = boxes[:, 2] - boxes[:, 0]
            boxes[:, 3] = boxes[:, 3] - boxes[:, 1]  # Convert the box formate to [Xmin,Ymin,W,H]

            result = {  # Store the image id and boxes and scores in result dict.
                'image_id': image_id,
                'PredictionString': format_prediction_string(boxes, scores)
            }

            results.append(result)  # Append the result dict to Results list
        outs.append({
            'image': images[0].permute(1, 2, 0).cpu().numpy().copy(),
            'boxes': outputs[0]['boxes'].data.cpu().numpy(),
            'scores': outputs[0]['scores'].data.cpu().numpy()
        })

    test_df = pd.DataFrame(results, columns=['image_id', 'PredictionString'])
    test_df.head()

    for batch in outs:
        sample = batch['image']
        boxes = batch['boxes']
        scores = batch['scores']

        boxes = boxes[scores >= detection_threshold].astype(np.int32)

        fig, ax = plt.subplots(1, 1, figsize=(16, 8))

        for box in boxes:
            print(box)
            cv2.rectangle(sample,
                          (box[0], box[1]),
                          (box[2], box[3]),
                          (220, 0, 0), 2)

        ax.set_axis_off()
        ax.imshow(sample)
        plt.show()

    sample = images[0].permute(1, 2, 0).cpu().numpy().copy()
    boxes = outputs[0]['boxes'].data.cpu().numpy()
    scores = outputs[0]['scores'].data.cpu().numpy()

    boxes = boxes[scores >= detection_threshold].astype(np.int32)

    fig, ax = plt.subplots(1, 1, figsize=(16, 8))

    for box in boxes:
        print(box)
        cv2.rectangle(sample,
                      (box[0], box[1]),
                      (box[2], box[3]),
                      (220, 0, 0), 2)

    ax.set_axis_off()
    ax.imshow(sample)
    plt.show()

    test_df.to_csv('submission.csv', index=False)
