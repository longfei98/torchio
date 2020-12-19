import os
import ast
import gzip
import shutil
import tempfile
from pathlib import Path
from typing import Union, Iterable, Tuple, Any, Optional, List, Sequence

from torch.utils.data._utils.collate import default_collate
import numpy as np
import nibabel as nib
import SimpleITK as sitk
from tqdm import trange

from .constants import INTENSITY
from .typing import TypeNumber, TypePath


def to_tuple(
        value: Union[TypeNumber, Iterable[TypeNumber]],
        length: int = 1,
        ) -> Tuple[TypeNumber, ...]:
    """
    to_tuple(1, length=1) -> (1,)
    to_tuple(1, length=3) -> (1, 1, 1)

    If value is an iterable, n is ignored and tuple(value) is returned
    to_tuple((1,), length=1) -> (1,)
    to_tuple((1, 2), length=1) -> (1, 2)
    to_tuple([1, 2], length=3) -> (1, 2)
    """
    try:
        iter(value)
        value = tuple(value)
    except TypeError:
        value = length * (value,)
    return value


def get_stem(
        path: Union[TypePath, List[TypePath]]
        ) -> Union[str, List[str]]:
    """
    '/home/user/image.nii.gz' -> 'image'
    """
    def _get_stem(path_string):
        return Path(path_string).name.split('.')[0]
    if isinstance(path, (str, Path)):
        return _get_stem(path)
    return [_get_stem(p) for p in path]


def create_dummy_dataset(
        num_images: int,
        size_range: Tuple[int, int],
        directory: Optional[TypePath] = None,
        suffix: str = '.nii.gz',
        force: bool = False,
        verbose: bool = False,
        ):
    from .data import ScalarImage, LabelMap, Subject
    output_dir = tempfile.gettempdir() if directory is None else directory
    output_dir = Path(output_dir)
    images_dir = output_dir / 'dummy_images'
    labels_dir = output_dir / 'dummy_labels'

    if force:
        shutil.rmtree(images_dir)
        shutil.rmtree(labels_dir)

    subjects: List[Subject] = []
    if images_dir.is_dir():
        for i in trange(num_images):
            image_path = images_dir / f'image_{i}{suffix}'
            label_path = labels_dir / f'label_{i}{suffix}'
            subject = Subject(
                one_modality=ScalarImage(image_path),
                segmentation=LabelMap(label_path),
            )
            subjects.append(subject)
    else:
        images_dir.mkdir(exist_ok=True, parents=True)
        labels_dir.mkdir(exist_ok=True, parents=True)
        if verbose:
            print('Creating dummy dataset...')  # noqa: T001
            iterable = trange(num_images)
        else:
            iterable = range(num_images)
        for i in iterable:
            shape = np.random.randint(*size_range, size=3)
            affine = np.eye(4)
            image = np.random.rand(*shape)
            label = np.ones_like(image)
            label[image < 0.33] = 0
            label[image > 0.66] = 2
            image *= 255

            image_path = images_dir / f'image_{i}{suffix}'
            nii = nib.Nifti1Image(image.astype(np.uint8), affine)
            nii.to_filename(str(image_path))

            label_path = labels_dir / f'label_{i}{suffix}'
            nii = nib.Nifti1Image(label.astype(np.uint8), affine)
            nii.to_filename(str(label_path))

            subject = Subject(
                one_modality=ScalarImage(image_path),
                segmentation=LabelMap(label_path),
            )
            subjects.append(subject)
    return subjects


def apply_transform_to_file(
        input_path: TypePath,
        transform,  # : Transform seems to create a circular import
        output_path: TypePath,
        type: str = INTENSITY,  # noqa: A002
        verbose: bool = False,
        ):
    from . import Image, Subject
    subject = Subject(image=Image(input_path, type=type))
    transformed = transform(subject)
    transformed.image.save(output_path)
    if verbose and transformed.history:
        print('Applied transform:', transformed.history[0])  # noqa: T001


def guess_type(string: str) -> Any:
    # Adapted from
    # https://www.reddit.com/r/learnpython/comments/4599hl/module_to_guess_type_from_a_string/czw3f5s
    string = string.replace(' ', '')
    try:
        value = ast.literal_eval(string)
    except ValueError:
        result_type = str
    else:
        result_type = type(value)
    if result_type in (list, tuple):
        string = string[1:-1]  # remove brackets
        split = string.split(',')
        list_result = [guess_type(n) for n in split]
        value = tuple(list_result) if result_type is tuple else list_result
        return value
    try:
        value = result_type(string)
    except TypeError:
        value = None
    return value


def get_torchio_cache_dir():
    return Path('~/.cache/torchio').expanduser()


def round_up(value: float) -> int:
    """Round half towards infinity.

    Args:
        value: The value to round.

    Example:

        >>> round(2.5)
        2
        >>> round(3.5)
        4
        >>> round_up(2.5)
        3
        >>> round_up(3.5)
        4

    """
    return int(np.floor(value + 0.5))


def compress(input_path, output_path):
    with open(input_path, 'rb') as f_in:
        with gzip.open(output_path, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)


def check_sequence(sequence: Sequence, name: str):
    try:
        iter(sequence)
    except TypeError:
        message = f'"{name}" must be a sequence, not {type(name)}'
        raise TypeError(message)


def get_major_sitk_version() -> int:
    # This attribute was added in version 2
    # https://github.com/SimpleITK/SimpleITK/pull/1171
    version = getattr(sitk, '__version__', None)
    major_version = 1 if version is None else 2
    return major_version


def history_collate(batch: Sequence, collate_transforms=True):
    attr = 'history' if collate_transforms else 'applied_transforms'
    # Adapted from
    # https://github.com/romainVala/torchQC/blob/master/segmentation/collate_functions.py
    from .data import Subject
    first_element = batch[0]
    if isinstance(first_element, Subject):
        dictionary = {
            key: default_collate([d[key] for d in batch])
            for key in first_element
        }
        if hasattr(first_element, attr):
            dictionary.update({attr: [getattr(d, attr) for d in batch]})
        return dictionary


# Adapted from torchvision, removing print statements
def download_and_extract_archive(
        url: str,
        download_root: TypePath,
        extract_root: Optional[TypePath] = None,
        filename: Optional[TypePath] = None,
        md5: str = None,
        remove_finished: bool = False,
        ) -> None:
    download_root = os.path.expanduser(download_root)
    if extract_root is None:
        extract_root = download_root
    if not filename:
        filename = os.path.basename(url)
    download_url(url, download_root, filename, md5)
    archive = os.path.join(download_root, filename)
    from torchvision.datasets.utils import extract_archive
    extract_archive(archive, extract_root, remove_finished)


# Adapted from torchvision, removing print statements
def download_url(
        url: str,
        root: TypePath,
        filename: Optional[TypePath] = None,
        md5: str = None,
        ) -> None:
    """Download a file from a url and place it in root.

    Args:
        url: URL to download file from
        root: Directory to place downloaded file in
        filename: Name to save the file under.
            If ``None``, use the basename of the URL
        md5: MD5 checksum of the download. If None, do not check
    """
    import urllib
    from torchvision.datasets.utils import check_integrity, gen_bar_updater

    root = os.path.expanduser(root)
    if not filename:
        filename = os.path.basename(url)
    fpath = os.path.join(root, filename)
    os.makedirs(root, exist_ok=True)
    # check if file is already present locally
    if not check_integrity(fpath, md5):
        try:
            print('Downloading ' + url + ' to ' + fpath)  # noqa: T001
            urllib.request.urlretrieve(
                url, fpath,
                reporthook=gen_bar_updater()
            )
        except (urllib.error.URLError, OSError) as e:
            if url[:5] == 'https':
                url = url.replace('https:', 'http:')
                message = (
                    'Failed download. Trying https -> http instead.'
                    ' Downloading ' + url + ' to ' + fpath
                )
                print(message)  # noqa: T001
                urllib.request.urlretrieve(
                    url, fpath,
                    reporthook=gen_bar_updater()
                )
            else:
                raise e
        # check integrity of downloaded file
        if not check_integrity(fpath, md5):
            raise RuntimeError('File not found or corrupted.')
