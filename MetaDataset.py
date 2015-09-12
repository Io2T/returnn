
from Dataset import Dataset, DatasetSeq, init_dataset
from CachedDataset2 import CachedDataset2
from Util import NumbersDict, load_json


class MetaDataset(CachedDataset2):

  def __init__(self,
               seq_list_file, seq_lens_file,
               datasets,
               data_map, data_dims,
               data_1_of_k=None, data_dtypes=None,
               window=1, **kwargs):
    """
    :param str seq_list_file: filename. line-separated
    :param str seq_lens_file: filename. json. dict[str,dict[str,int]], seq-tag -> data-key -> len
    :param dict[str,dict[str]] datasets: dataset-key -> dataset-kwargs. including keyword 'class' and maybe 'files'
    :param dict[str,(str,str)] data_map: self-data-key -> (dataset-key, dataset-data-key).
      Should contain 'data' as key. Also defines the target-list, which is all except 'data'.
    :param dict[str,int] data_dims: self-data-key -> data-dimension.
    :param dict[str,bool] data_1_of_k: self-data-key -> whether it is 1-of-k or not. automatic if not specified
    :param dict[str,str] data_dtypes: self-data-key -> dtype. automatic if not specified
    """
    assert window == 1  # not implemented
    super(MetaDataset, self).__init__(**kwargs)
    assert self.shuffle_frames_of_nseqs == 0  # not implemented. anyway only for non-recurrent nets

    self.seq_list_original = open(seq_list_file).read().splitlines()
    self.tag_idx = {tag: idx for (idx, tag) in enumerate(self.seq_list_original)}
    self._num_seqs = len(self.seq_list_original)

    self.data_map = data_map
    self.dataset_keys = set([m[0] for m in self.data_map.values()]); ":type: set[str]"
    self.data_keys = set(self.data_map.keys()); ":type: set[str]"
    assert "data" in self.data_keys
    self.target_list = sorted(self.data_keys - ["data"])

    self.data_dims = data_dims
    for v in data_dims.values():
      assert isinstance(v, int), "all values must be int in %r" % data_dims
    assert "data" in data_dims
    self.num_inputs = data_dims["data"]
    self.num_outputs = {data_key: data_dims[data_key] for data_key in self.target_list}

    self.data_1_of_k = {data_key: _select_1_of_k(data_key, data_1_of_k, data_dtypes) for data_key in self.data_keys}
    self.data_dtypes = {data_key: _select_dtype(data_key, self.data_1_of_k, data_dtypes) for data_key in self.data_keys}

    if seq_lens_file:
      seq_lens = load_json(filename=seq_lens_file)
      assert isinstance(seq_lens, dict)
      # dict[str,NumbersDict], seq-tag -> data-key -> len
      self._seq_lens = {tag: NumbersDict(l) for (tag, l) in seq_lens.items()}
    else:
      self._seq_lens = None

    if self._seq_lens:
      self._num_timesteps = sum([self._seq_lens[s] for s in self.seq_list_original])
    else:
      self._num_timesteps = None

    # Will only init the needed datasets.
    self.datasets = {key: init_dataset(datasets[key]) for key in self.dataset_keys}

  def init_seq_order(self, epoch=None, seq_list=None):
    need_reinit = self.epoch == epoch
    super(MetaDataset, self).init_seq_order(epoch=epoch, seq_list=seq_list)
    if not need_reinit:
      return

    if seq_list:
      seq_index = [self.tag_idx[tag] for tag in seq_list]
    else:
      if self._seq_lens:
        get_seq_len = lambda s: self._seq_lens[self.seq_list_original[s]]["data"]
      else:
        get_seq_len = None
      seq_index = self.get_seq_order_for_epoch(epoch, self.num_seqs, get_seq_len)
    self.seq_list_ordered = [self.seq_list_original[s] for s in seq_index]

    for dataset in self.datasets.values():
      dataset.init_seq_order(epoch=epoch, seq_list=self.seq_list_ordered)

  def _load_seqs(self, start, end):
    for dataset in self.datasets.values():
      dataset.load_seqs(start, end)
      for seq_idx in range(start, end):
        self._check_dataset_seq(dataset, seq_idx)
    super(MetaDataset, self)._load_seqs(start=start, end=end)

  def _check_dataset_seq(self, dataset, seq_idx):
    """
    :type dataset: Dataset
    :type seq_idx: int
    """
    dataset_seq_tag = dataset.get_tag(seq_idx)
    self_seq_tag = self.get_tag(seq_idx)
    assert dataset_seq_tag == self_seq_tag

  def _get_data(self, seq_idx, data_key):
    """
    :type seq_idx: int
    :type data_key: str
    :rtype: numpy.ndarray
    """
    dataset_key, dataset_data_key = self.data_map[data_key]
    dataset = self.datasets[dataset_key]; ":type: Dataset"
    return dataset.get_data(seq_idx, dataset_data_key)

  def _collect_single_seq(self, seq_idx):
    """
    :type seq_idx: int
    :rtype: DatasetSeq
    """
    seq_tag = self.seq_list_ordered[seq_idx]
    features = self._get_data(seq_idx, "data")
    targets = {target: self._get_data(seq_idx, target) for target in self.target_list}
    return DatasetSeq(seq_idx=seq_idx, seq_tag=seq_tag, features=features, targets=targets)

  def get_seq_length(self, sorted_seq_idx):
    if self._seq_lens:
      return self._seq_lens[self.seq_list_ordered[sorted_seq_idx]]
    return super(MetaDataset, self).get_seq_length(sorted_seq_idx)

  def get_tag(self, sorted_seq_idx):
    return self.seq_list_ordered[sorted_seq_idx]

  def get_target_list(self):
    return self.target_list

  def get_data_dim(self, key):
    """
    :type key: str
    :return: 1 for hard labels, num_outputs[target] for soft labels
    """
    if self.data_1_of_k[key]:
      d = 1
    else:
      d = self.data_dims[key]
    if self.added_data:
      assert super(MetaDataset, self).get_data_dim(key) == d
    return d

  def get_data_dtype(self, key):
    dtype = self.data_dtypes[key]
    if self.added_data:
      assert super(MetaDataset, self).get_data_dtype(key) == dtype
    return dtype


class ConcatDataset(CachedDataset2):
  def __init__(self, datasets, **kwargs):
    """
    :param list[dict[str]] datasets: list of kwargs for init_dataset
    """
    super(ConcatDataset, self).__init__(**kwargs)
    self.datasets = [init_dataset(d_kwargs) for d_kwargs in datasets]

  def init_seq_order(self, epoch=None, seq_list=None):
    """
    :type epoch: int|None
    :param list[str] | None seq_list: In case we want to set a predefined order.
    """
    need_reinit = self.epoch == epoch
    super(ConcatDataset, self).init_seq_order(epoch=epoch, seq_list=seq_list)
    self.dataset_seq_idx_offsets = [0]
    if not need_reinit:
      return

    if seq_list:  # reference order
      seq_lists = []
      for dataset in self.datasets:
        # This depends on the num_seqs of our childs.
        seq_lists += seq_list[:dataset.num_seqs]
        seq_list = seq_list[dataset.num_seqs:]
      assert len(seq_list) == 0  # we have consumed all
    else:
      seq_lists = [None] * len(self.datasets)
      if self.seq_ordering == "sorted":
        # Not sure about this case. Maybe a separate implementation makes more sense.
        raise NotImplementedError

    assert len(seq_lists) == len(self.datasets)
    for dataset, sub_list in zip(self.datasets, seq_lists):
      dataset.init_seq_order(epoch=epoch, seq_list=sub_list)

  def _get_dataset_for_seq_idx(self, seq_idx):
    i = 0
    while i < len(self.dataset_seq_idx_offsets):
      if seq_idx + self.dataset_seq_idx_offsets[i] < 0:
        return i - 1
      i += 1
    return i - 1

  def _load_seqs(self, start, end):
    sub_start = start
    while True:
      dataset_idx = self._get_dataset_for_seq_idx(sub_start)
      dataset = self.datasets[dataset_idx]
      dataset_seq_idx_start = sub_start + self.dataset_seq_idx_offsets[dataset_idx]
      dataset_seq_idx_end = end + self.dataset_seq_idx_offsets[dataset_idx]
      dataset.load_seqs(dataset_seq_idx_start, dataset_seq_idx_end)
      if dataset.is_less_than_num_seqs(dataset_seq_idx_end):
        self.dataset_seq_idx_offsets[dataset_idx + 1:dataset_idx + 2] = [
          self.dataset_seq_idx_offsets[dataset_idx] - dataset.num_seqs]
        sub_start = -self.dataset_seq_idx_offsets[dataset_idx + 1]
      else:
        break
    super(ConcatDataset, self)._load_seqs(start=start, end=end)

  def _collect_single_seq(self, seq_idx):
    dataset_idx = self._get_dataset_for_seq_idx(seq_idx)
    dataset = self.datasets[dataset_idx]
    dataset_seq_idx = seq_idx + self.dataset_seq_idx_offsets[dataset_idx]
    seq_tag = dataset.get_tag(dataset_seq_idx)
    features = dataset.get_input_data(dataset_seq_idx)
    targets = {k: dataset.get_targets(k, dataset_seq_idx) for k in dataset.get_target_list()}
    return DatasetSeq(seq_idx=seq_idx, seq_tag=seq_tag, features=features, targets=targets)

  @property
  def num_seqs(self):
    return sum([ds.num_seqs for ds in self.datasets])

  def get_target_list(self):
    return self.datasets[0].get_target_list()


def _simple_to_bool(v):
  if v == 0: v = False
  if v == 1: v = True
  assert isinstance(v, bool)
  return v

def _select_1_of_k(key, data_1_of_k, data_dtypes):
  if data_1_of_k and key in data_1_of_k:
    v = data_1_of_k[key]
    return _simple_to_bool(v)
  if data_dtypes and key in data_dtypes:
    v = data_dtypes[key]
    if v.startswith("int"):
      return True  # int is likely a 1-of-k
    return False
  if key == "data":
    return False  # the data (input) is likely not 1-of-k
  return True  # all targets are likely 1-of-k encoded (for classification)

def _select_dtype(key, data_1_of_k, data_dtypes):
  if data_dtypes and key in data_dtypes:
    v = data_dtypes[key]
    assert isinstance(v, str)  # e.g. "int32" or "float32"
    return v
  if data_1_of_k and key in data_1_of_k:
    if data_1_of_k[key]:
      return "int32"  # standard for 1-of-k
    else:
      return "float32"  # standard otherwise
  if key == "data":
    return "float32"  # standard for input
  return "int32"  # all targets are likely 1-of-k encoded (for classification)

