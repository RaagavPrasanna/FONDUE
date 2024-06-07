import torch
import torch.nn as nn
import torch.nn.functional as F
import lpips
import torchvision
from math import ceil as ceil
# from skimage.metrics import structural_similarity as ssim
# from sobel import CannyLoss
# from pytorch_msssim import ssim, ms_ssim, SSIM, MS_SSIM
# import numpy

try:
    from LovaszSoftmax.pytorch.lovasz_losses import lovasz_hinge
except ImportError:
    pass

__all__ = ['BCEDiceLoss', 'LovaszHingeLoss', 'MSELoss','VGGWPL', 'LPIPS', 'HDRFLIPLossLPIPS', 'LPIPS_regularized', 'GANLoss', 'GradientPenaltyLoss', 'TV_L2Loss', 'TV_L1Loss', 'TV_L1LOSS']


import sys
import numpy as np
import torch
import torch.nn as nn

# Define GAN loss: [vanilla | lsgan | wgan-gp]
class GANLoss(nn.Module):
    def __init__(self, gan_type, real_label_val=1.0, fake_label_val=0.0):
        super(GANLoss, self).__init__()
        self.gan_type = gan_type.lower()
        self.real_label_val = real_label_val
        self.fake_label_val = fake_label_val

        if self.gan_type == 'gan' or self.gan_type == 'ragan':
            self.loss = nn.BCEWithLogitsLoss()
        elif self.gan_type == 'lsgan':
            self.loss = nn.MSELoss()
        elif self.gan_type == 'wgan-gp':

            def wgan_loss(input, target):
                # target is boolean
                return -1 * input.mean() if target else input.mean()

            self.loss = wgan_loss
        else:
            raise NotImplementedError('GAN type [{:s}] is not found'.format(self.gan_type))

    def get_target_label(self, input, target_is_real):
        if self.gan_type == 'wgan-gp':
            return target_is_real
        if target_is_real:
            return torch.empty_like(input).fill_(self.real_label_val)
        else:
            return torch.empty_like(input).fill_(self.fake_label_val)

    def forward(self, input, target_is_real):
        target_label = self.get_target_label(input, target_is_real)
        loss = self.loss(input, target_label)
        return loss


class GradientPenaltyLoss(nn.Module):
    def __init__(self, device=torch.device('cpu')):
        super(GradientPenaltyLoss, self).__init__()
        self.register_buffer('grad_outputs', torch.Tensor())
        self.grad_outputs = self.grad_outputs.to(device)

    def get_grad_outputs(self, input):
        if self.grad_outputs.size() != input.size():
            self.grad_outputs.resize_(input.size()).fill_(1.0)
        return self.grad_outputs

    def forward(self, interp, interp_crit):
        grad_outputs = self.get_grad_outputs(interp_crit)
        grad_interp = torch.autograd.grad(outputs=interp_crit, inputs=interp,
                                          grad_outputs=grad_outputs, create_graph=True,
                                          retain_graph=True, only_inputs=True)[0]
        grad_interp = grad_interp.view(grad_interp.size(0), -1)
        grad_interp_norm = grad_interp.norm(2, dim=1)

        loss = ((grad_interp_norm - 1)**2).mean()
        return loss

class TV_L2Loss(nn.Module):
    def __init__(self):
        super(TV_L2Loss, self).__init__()

    def forward(self, x):
        batch_size = x.size()[0]
        h_x = x.size()[2]
        w_x = x.size()[3]
        count_h = self.tensor_size(x[:, :, 1:, :])
        count_w = self.tensor_size(x[:, :, :, 1:])
        h_tv = torch.pow((x[:, :, 1:, :] - x[:, :, :h_x - 1, :]), 2).sum()
        w_tv = torch.pow((x[:, :, :, 1:] - x[:, :, :, :w_x - 1]), 2).sum()
        return  (h_tv / count_h + w_tv / count_w) / batch_size

    def tensor_size(self, t):
        return t.size()[1] * t.size()[2] * t.size()[3]

class TV_L1Loss(nn.Module):
    def __init__(self, tv_loss_weight=1):
        super(TV_L1Loss, self).__init__()

    def forward(self, x):
        batch_size = x.size()[0]
        h_x = x.size()[2]
        w_x = x.size()[3]
        count_h = self.tensor_size(x[:, :, 1:, :])
        count_w = self.tensor_size(x[:, :, :, 1:])
        h_tv = torch.abs((x[:, :, 1:, :] - x[:, :, :h_x - 1, :])).sum()
        w_tv = torch.abs((x[:, :, :, 1:] - x[:, :, :, :w_x - 1])).sum()
        return (h_tv / count_h + w_tv / count_w) / batch_size

    def tensor_size(self, t):
        return t.size()[1] * t.size()[2] * t.size()[3]

class TV_L1LOSS(nn.Module):
    def __init__(self):
        super(TV_L1LOSS,self).__init__()

    def forward(self, x, y):
        size = x.size()
        h_tv_diff = torch.abs((x[:, :, 1:, :] - x[:, :, :-1, :] - (y[:, :, 1:, :] - y[:, :, :-1, :]))).sum()
        w_tv_diff = torch.abs((x[:, :, :, 1:] - x[:, :, :, :-1] - (y[:, :, :, 1:] - y[:, :, :, :-1]))).sum()
        return (h_tv_diff + w_tv_diff) / size[0] / size[1] / size[2] / size[3]

class HDRFLIPLossLPIPS(nn.Module):
    """ Class for computing HDR-FLIP """

    def __init__(self):
        """ Init """
        super().__init__()
        self.qc = 0.7
        self.qf = 0.5
        self.pc = 0.4
        self.pt = 0.95
        self.tmax = 0.85
        self.tmin = 0.85
        self.eps = 1e-15

    def forward(self, test, reference, lpips_loss, pixels_per_degree=(0.7 * 3840 / 0.7) * np.pi / 180, tone_mapper="aces", start_exposure=None, stop_exposure=None):
        """
        Computes the HDR-FLIP error map between two HDR images,
        assuming the images are observed at a certain number of
        pixels per degree of visual angle

        :param test: test tensor (with NxCxHxW layout with nonnegative values)
        :param reference: reference tensor (with NxCxHxW layout with nonnegative values)
        :param pixels_per_degree: float describing the number of pixels per degree of visual angle of the observer,
                                  default corresponds to viewing the images on a 0.7 meters wide 4K monitor at 0.7 meters from the display
        :param tone_mapper: (optional) string describing what tone mapper HDR-FLIP should assume
        :param start_exposure: (optional tensor (with Nx1x1x1 layout) with start exposures corresponding to each HDR reference/test pair
        :param stop_exposure: (optional) tensor (with Nx1x1x1 layout) with stop exposures corresponding to each HDR reference/test pair
        :return: float containing the mean FLIP error (in the range [0,1]) between the HDR reference and test images in the batch
        """
        
        # HDR-FLIP expects nonnegative and non-NaN values in the input
        reference = torch.clamp(reference, 0, 65536.0)
        test = torch.clamp(test, 0, 65536.0)
        if reference.shape[1] == 1:
            reference = reference.repeat(1, 3, 1, 1)
        if test.shape[1] == 1:
            test = test.repeat(1, 3, 1, 1)
        # Compute start and stop exposures, if they are not given
        if start_exposure is None or stop_exposure is None:
            c_start, c_stop = compute_start_stop_exposures(reference, tone_mapper, self.tmax, self.tmin)
            if start_exposure is None:
                start_exposure = c_start
            if stop_exposure is None:
                stop_exposure = c_stop

        # Compute number of exposures
        num_exposures = torch.max(torch.tensor([2.0], requires_grad=False).cuda(), torch.ceil(stop_exposure - start_exposure))
        most_exposures = int(torch.amax(num_exposures, dim=0).item())

        # Compute exposure step size
        step_size = (stop_exposure - start_exposure) / torch.max(num_exposures - 1, torch.tensor([1.0], requires_grad=False).cuda())

        # Set the depth of the error tensor to the number of exposures given by the largest exposure range any reference image yielded.
        # This allows us to do one loop for each image in our batch, while not affecting the HDR-FLIP error, as we fill up the error tensor with 0s.
        # Note that the step size still depends on num_exposures and is therefore independent of most_exposures
        dim = reference.size()
        all_errors = torch.zeros(size=(dim[0], most_exposures, dim[2], dim[3])).cuda()

        # Loop over exposures and compute LDR-FLIP for each pair of LDR reference and test
        for i in range(0, most_exposures):
            exposure = start_exposure + i * step_size

            reference_tone_mapped = tone_map(reference, tone_mapper, exposure)
            test_tone_mapped = tone_map(test, tone_mapper, exposure)

            reference_opponent = color_space_transform(reference_tone_mapped, 'linrgb2ycxcz')
            test_opponent = color_space_transform(test_tone_mapped, 'linrgb2ycxcz')

            all_errors[:, i, :, :] = compute_ldrflip(
                test_opponent, reference_opponent, pixels_per_degree,
                self.qc, self.qf, self.pc, self.pt, self.eps
            ).squeeze(1)

        # Take per-pixel maximum over all LDR-FLIP errors to get HDR-FLIP
        hdrflip_error = torch.amax(all_errors, dim=1, keepdim=True)
        lpips_error = lpips_loss(test[:,1,:,:].unsqueeze(1), reference[:,1,:,:].unsqueeze(1))
        regularized_error = hdrflip_error * lpips_error
        loss = torch.mean(regularized_error)
        if torch.isnan(loss):
            is_nan = True
            loss = 1
        else:
            is_nan = False
        return loss, is_nan


class LDRFLIPLoss(nn.Module):
    """ Class for computing LDR FLIP loss """

    def __init__(self):
        """ Init """
        super().__init__()
        self.qc = 0.7
        self.qf = 0.5
        self.pc = 0.4
        self.pt = 0.95
        self.eps = 1e-15
        

    def forward(self, test, reference, pixels_per_degree=(0.7 * 3840 / 0.7) * np.pi / 180):
        """
        Computes the LDR-FLIP error map between two LDR images,
        assuming the images are observed at a certain number of
        pixels per degree of visual angle

        :param test: test tensor (with NxCxHxW layout with values in the range [0, 1] in the sRGB color space)
        :param reference: reference tensor (with NxCxHxW layout with values in the range [0, 1] in the sRGB color space)
        :param pixels_per_degree: float describing the number of pixels per degree of visual angle of the observer,
                                  default corresponds to viewing the images on a 0.7 meters wide 4K monitor at 0.7 meters from the display
        :return: float containing the mean FLIP error (in the range [0,1]) between the LDR reference and test images in the batch
        """
        # LDR-FLIP expects non-NaN values in [0,1] as input
        reference = torch.clamp(reference, 0, 1)
        test = torch.clamp(test, 0, 1)

        # Transform reference and test to opponent color space
        reference_opponent = color_space_transform(reference, 'srgb2ycxcz')
        test_opponent = color_space_transform(test, 'srgb2ycxcz')

        deltaE = compute_ldrflip(test_opponent, reference_opponent, pixels_per_degree, self.qc, self.qf, self.pc, self.pt, self.eps)

        return torch.mean(deltaE)


def compute_ldrflip(test, reference, pixels_per_degree, qc, qf, pc, pt, eps):
    """
    Computes the LDR-FLIP error map between two LDR images,
    assuming the images are observed at a certain number of
    pixels per degree of visual angle

    :param reference: reference tensor (with NxCxHxW layout with values in the YCxCz color space)
    :param test: test tensor (with NxCxHxW layout with values in the YCxCz color space)
    :param pixels_per_degree: float describing the number of pixels per degree of visual angle of the observer,
                              default corresponds to viewing the images on a 0.7 meters wide 4K monitor at 0.7 meters from the display
    :param qc: float describing the q_c exponent in the LDR-FLIP color pipeline (see FLIP paper for details)
    :param qf: float describing the q_f exponent in the LDR-FLIP feature pipeline (see FLIP paper for details)
    :param pc: float describing the p_c exponent in the LDR-FLIP color pipeline (see FLIP paper for details)
    :param pt: float describing the p_t exponent in the LDR-FLIP color pipeline (see FLIP paper for details)
    :param eps: float containing a small value used to improve training stability
    :return: tensor containing the per-pixel FLIP errors (with Nx1xHxW layout and values in the range [0, 1]) between LDR reference and test images
    """
    # --- Color pipeline ---
    # Spatial filtering
    s_a, radius_a = generate_spatial_filter(pixels_per_degree, 'A')
    s_rg, radius_rg = generate_spatial_filter(pixels_per_degree, 'RG')
    s_by, radius_by = generate_spatial_filter(pixels_per_degree, 'BY')
    radius = max(radius_a, radius_rg, radius_by)
    filtered_reference = spatial_filter(reference, s_a, s_rg, s_by, radius)
    filtered_test = spatial_filter(test, s_a, s_rg, s_by, radius)

    # Perceptually Uniform Color Space
    preprocessed_reference = hunt_adjustment(color_space_transform(filtered_reference, 'linrgb2lab'))
    preprocessed_test = hunt_adjustment(color_space_transform(filtered_test, 'linrgb2lab'))

    # Color metric
    deltaE_hyab = hyab(preprocessed_reference, preprocessed_test, eps)
    power_deltaE_hyab = torch.pow(deltaE_hyab, qc)
    hunt_adjusted_green = hunt_adjustment(color_space_transform(torch.tensor([[[0.0]], [[1.0]], [[0.0]]]).unsqueeze(0), 'linrgb2lab'))
    hunt_adjusted_blue = hunt_adjustment(color_space_transform(torch.tensor([[[0.0]], [[0.0]], [[1.0]]]).unsqueeze(0), 'linrgb2lab'))
    cmax = torch.pow(hyab(hunt_adjusted_green, hunt_adjusted_blue, eps), qc).item()
    deltaE_c = redistribute_errors(power_deltaE_hyab, cmax, pc, pt)

    # --- Feature pipeline ---
    # Extract and normalize Yy component
    ref_y = (reference[:, 0:1, :, :] + 16) / 116
    test_y = (test[:, 0:1, :, :] + 16) / 116

    # Edge and point detection
    edges_reference = feature_detection(ref_y, pixels_per_degree, 'edge')
    points_reference = feature_detection(ref_y, pixels_per_degree, 'point')
    edges_test = feature_detection(test_y, pixels_per_degree, 'edge')
    points_test = feature_detection(test_y, pixels_per_degree, 'point')

    # Feature metric
    deltaE_f = torch.max(
        torch.abs(torch.norm(edges_reference, dim=1, keepdim=True) - torch.norm(edges_test, dim=1, keepdim=True)),
        torch.abs(torch.norm(points_test, dim=1, keepdim=True) - torch.norm(points_reference, dim=1, keepdim=True))
    )
    deltaE_f = torch.clamp(deltaE_f, min=eps)  # clamp to stabilize training
    deltaE_f = torch.pow(((1 / np.sqrt(2)) * deltaE_f), qf)

    # --- Final error ---
    return torch.pow(deltaE_c, 1 - deltaE_f)


def tone_map(img, tone_mapper, exposure):
    """
    Applies exposure compensation and tone mapping.
    Refer to the Visualizing Errors in Rendered High Dynamic Range Images
    paper for details about the formulas.

    :param img: float tensor (with NxCxHxW layout) containing nonnegative values
    :param tone_mapper: string describing the tone mapper to apply
    :param exposure: float tensor (with Nx1x1x1 layout) describing the exposure compensation factor
    """
    # Exposure compensation
    x = (2 ** exposure) * img

    # Set tone mapping coefficients depending on tone_mapper
    if tone_mapper == "reinhard":
        lum_coeff_r = 0.2126
        lum_coeff_g = 0.7152
        lum_coeff_b = 0.0722

        Y = x[:, 0:1, :, :] * lum_coeff_r + x[:, 1:2, :, :] * lum_coeff_g + x[:, 2:3, :, :] * lum_coeff_b
        return torch.clamp(torch.div(x, 1 + Y), 0.0, 1.0)

    if tone_mapper == "hable":
        # Source: https://64.github.io/tonemapping/
        A = 0.15
        B = 0.50
        C = 0.10
        D = 0.20
        E = 0.02
        F = 0.30
        k0 = A * F - A * E
        k1 = C * B * F - B * E
        k2 = 0
        k3 = A * F
        k4 = B * F
        k5 = D * F * F

        W = 11.2
        nom = k0 * torch.pow(W, torch.tensor([2.0]).cuda()) + k1 * W + k2
        denom = k3 * torch.pow(W, torch.tensor([2.0]).cuda()) + k4 * W + k5
        white_scale = torch.div(denom, nom)  # = 1 / (nom / denom)

        # Include white scale and exposure bias in rational polynomial coefficients
        k0 = 4 * k0 * white_scale
        k1 = 2 * k1 * white_scale
        k2 = k2 * white_scale
        k3 = 4 * k3
        k4 = 2 * k4
        # k5 = k5 # k5 is not changed
    else:
        # Source:  ACES approximation: https://knarkowicz.wordpress.com/2016/01/06/aces-filmic-tone-mapping-curve/
        # Include pre-exposure cancelation in constants
        k0 = 0.6 * 0.6 * 2.51
        k1 = 0.6 * 0.03
        k2 = 0
        k3 = 0.6 * 0.6 * 2.43
        k4 = 0.6 * 0.59
        k5 = 0.14

    x2 = torch.pow(x, 2)
    nom = k0 * x2 + k1 * x + k2
    denom = k3 * x2 + k4 * x + k5
    denom = torch.where(torch.isinf(denom), torch.Tensor([1.0]).cuda(), denom)  # if denom is inf, then so is nom => nan. Pixel is very bright. It becomes inf here, but 1 after clamp below
    y = torch.div(nom, denom)
    return torch.clamp(y, 0.0, 1.0)


def compute_start_stop_exposures(reference, tone_mapper, tmax, tmin):
    """
    Computes start and stop exposure for HDR-FLIP based on given tone mapper and reference image.
    Refer to the Visualizing Errors in Rendered High Dynamic Range Images
    paper for details about the formulas

    :param reference: float tensor (with NxCxHxW layout) containing reference images (nonnegative values)
    :param tone_mapper: string describing which tone mapper should be assumed
    :param tmax: float describing the t value used to find the start exposure
    :param tmin: float describing the t value used to find the stop exposure
    :return: two float tensors (with Nx1x1x1 layout) containing start and stop exposures, respectively, to use for HDR-FLIP
    """
    if tone_mapper == "reinhard":
        k0 = 0
        k1 = 1
        k2 = 0
        k3 = 0
        k4 = 1
        k5 = 1

        x_max = tmax * k5 / (k1 - tmax * k4)
        x_min = tmin * k5 / (k1 - tmin * k4)
    elif tone_mapper == "hable":
        # Source: https://64.github.io/tonemapping/
        A = 0.15
        B = 0.50
        C = 0.10
        D = 0.20
        E = 0.02
        F = 0.30
        k0 = A * F - A * E
        k1 = C * B * F - B * E
        k2 = 0
        k3 = A * F
        k4 = B * F
        k5 = D * F * F

        W = 11.2
        nom = k0 * torch.pow(W, torch.tensor([2.0]).cuda()) + k1 * W + k2
        denom = k3 * torch.pow(W, torch.tensor([2.0]).cuda()) + k4 * W + k5
        white_scale = torch.div(denom, nom)  # = 1 / (nom / denom)

        # Include white scale and exposure bias in rational polynomial coefficients
        k0 = 4 * k0 * white_scale
        k1 = 2 * k1 * white_scale
        k2 = k2 * white_scale
        k3 = 4 * k3
        k4 = 2 * k4
        # k5 = k5 # k5 is not changed

        c0 = (k1 - k4 * tmax) / (k0 - k3 * tmax)
        c1 = (k2 - k5 * tmax) / (k0 - k3 * tmax)
        x_max = - 0.5 * c0 + torch.sqrt(((torch.tensor([0.5]).cuda() * c0) ** 2) - c1)

        c0 = (k1 - k4 * tmin) / (k0 - k3 * tmin)
        c1 = (k2 - k5 * tmin) / (k0 - k3 * tmin)
        x_min = - 0.5 * c0 + torch.sqrt(((torch.tensor([0.5]).cuda() * c0) ** 2) - c1)
    else:
        # Source:  ACES approximation: https://knarkowicz.wordpress.com/2016/01/06/aces-filmic-tone-mapping-curve/
        # Include pre-exposure cancelation in constants
        k0 = 0.6 * 0.6 * 2.51
        k1 = 0.6 * 0.03
        k2 = 0
        k3 = 0.6 * 0.6 * 2.43
        k4 = 0.6 * 0.59
        k5 = 0.14

        c0 = (k1 - k4 * tmax) / (k0 - k3 * tmax)
        c1 = (k2 - k5 * tmax) / (k0 - k3 * tmax)
        x_max = - 0.5 * c0 + torch.sqrt(((torch.tensor([0.5]).cuda() * c0) ** 2) - c1)

        c0 = (k1 - k4 * tmin) / (k0 - k3 * tmin)
        c1 = (k2 - k5 * tmin) / (k0 - k3 * tmin)
        x_min = - 0.5 * c0 + torch.sqrt(((torch.tensor([0.5]).cuda() * c0) ** 2) - c1)

    # Convert reference to luminance
    lum_coeff_r = 0.2126
    lum_coeff_g = 0.7152
    lum_coeff_b = 0.0722
    Y_reference = reference[:, 0:1, :, :] * lum_coeff_r + reference[:, 1:2, :, :] * lum_coeff_g + reference[:, 2:3, :, :] * lum_coeff_b

    # Compute start exposure
    Y_hi = torch.amax(Y_reference, dim=(2, 3), keepdim=True)
    start_exposure = torch.log2(x_max / Y_hi)

    # Compute stop exposure
    dim = Y_reference.size()
    Y_ref = Y_reference.view(dim[0], dim[1], dim[2]*dim[3])
    # Y_lo = torch.median(Y_ref, dim=2).values.unsqueeze(2).unsqueeze(3)
    
    
    # Find the indices of the non-zero elements in Y_ref along dim=2
    # nonzero_indices = torch.nonzero(Y_ref, dim=2, as_tuple=True)[2]
    # # Select the non-zero elements from Y_ref using index_select
    # Y_ref_nonzero = torch.index_select(Y_ref, 2, nonzero_indices)
    # # Compute the median of each row of Y_ref_nonzero along dim=2
    # Y_lo_nonzero = torch.median(Y_ref_nonzero, dim=2).values
    # # Insert two dimensions of size one at positions 2 and 3
    # Y_lo_nonzero = Y_lo_nonzero.unsqueeze(2).unsqueeze(3)
    
    # nonzero_indices = torch.nonzero(Y_reference)
    # nonzero_elements = Y_reference[nonzero_indices[:, 0], nonzero_indices[:, 1], nonzero_indices[:, 2], nonzero_indices[:, 3]]
    # median_result = torch.median(nonzero_elements, dim=0)
    # Y_lo = median_result.values
    # Y_lo = Y_lo.unsqueeze(1).unsqueeze(2).unsqueeze(3)
    
    # Y_ref_nan = torch.where(Y_ref == 0, torch.tensor(float('nan')), Y_ref)

    # # Use torch.nanmedian to get the median values and indices along the first dimension
    # median_result = torch.nanmedian(Y_ref_nan, dim=0, keepdim=True)
    
    # # Extract the values tensor from the namedtuple
    # Y_lo = median_result.values
    Y_ref_nan = torch.where(Y_ref == 0, torch.tensor(float('nan')).to('cuda'), Y_ref)

    # Compute the median over the non-NaN values
    Y_lo_nan = torch.nanmedian(Y_ref_nan, dim=2).values.unsqueeze(2).unsqueeze(3)
    
    
    # stop_exposure = torch.log2(x_min / Y_lo)
    stop_exposure = torch.log2(x_min / Y_lo_nan)

    return start_exposure, stop_exposure


def generate_spatial_filter(pixels_per_degree, channel):
    """
    Generates spatial contrast sensitivity filters with width depending on
    the number of pixels per degree of visual angle of the observer

    :param pixels_per_degree: float indicating number of pixels per degree of visual angle
    :param channel: string describing what filter should be generated
    :yield: Filter kernel corresponding to the spatial contrast sensitivity function of the given channel and kernel's radius
    """
    a1_A = 1
    b1_A = 0.0047
    a2_A = 0
    b2_A = 1e-5  # avoid division by 0
    a1_rg = 1
    b1_rg = 0.0053
    a2_rg = 0
    b2_rg = 1e-5  # avoid division by 0
    a1_by = 34.1
    b1_by = 0.04
    a2_by = 13.5
    b2_by = 0.025
    if channel == "A":  # Achromatic CSF
        a1 = a1_A
        b1 = b1_A
        a2 = a2_A
        b2 = b2_A
    elif channel == "RG":  # Red-Green CSF
        a1 = a1_rg
        b1 = b1_rg
        a2 = a2_rg
        b2 = b2_rg
    elif channel == "BY":  # Blue-Yellow CSF
        a1 = a1_by
        b1 = b1_by
        a2 = a2_by
        b2 = b2_by

    # Determine evaluation domain
    max_scale_parameter = max([b1_A, b2_A, b1_rg, b2_rg, b1_by, b2_by])
    r = np.ceil(3 * np.sqrt(max_scale_parameter / (2 * np.pi**2)) * pixels_per_degree)
    r = int(r)
    deltaX = 1.0 / pixels_per_degree
    x, y = np.meshgrid(range(-r, r + 1), range(-r, r + 1))
    z = (x * deltaX)**2 + (y * deltaX)**2

    # Generate weights
    g = a1 * np.sqrt(np.pi / b1) * np.exp(-np.pi**2 * z / b1) + a2 * np.sqrt(np.pi / b2) * np.exp(-np.pi**2 * z / b2)
    g = g / np.sum(g)
    g = torch.Tensor(g).unsqueeze(0).unsqueeze(0).cuda()

    return g, r


def spatial_filter(img, s_a, s_rg, s_by, radius):
    """
    Filters an image with channel specific spatial contrast sensitivity functions
    and clips result to the unit cube in linear RGB

    :param img: image tensor to filter (with NxCxHxW layout in the YCxCz color space)
    :param s_a: spatial filter matrix for the achromatic channel
    :param s_rg: spatial filter matrix for the red-green channel
    :param s_by: spatial filter matrix for the blue-yellow channel
    :return: input image (with NxCxHxW layout) transformed to linear RGB after filtering with spatial contrast sensitivity functions
    """
    dim = img.size()
    # Prepare image for convolution
    img_pad = torch.zeros((dim[0], dim[1], dim[2] + 2 * radius, dim[3] + 2 * radius), device='cuda')
    img_pad[:, 0:1, :, :] = nn.functional.pad(img[:, 0:1, :, :], (radius, radius, radius, radius), mode='replicate')
    img_pad[:, 1:2, :, :] = nn.functional.pad(img[:, 1:2, :, :], (radius, radius, radius, radius), mode='replicate')
    img_pad[:, 2:3, :, :] = nn.functional.pad(img[:, 2:3, :, :], (radius, radius, radius, radius), mode='replicate')

    # Apply Gaussian filters
    img_tilde_opponent = torch.zeros((dim[0], dim[1], dim[2], dim[3]), device='cuda')
    img_tilde_opponent[:, 0:1, :, :] = nn.functional.conv2d(img_pad[:, 0:1, :, :], s_a.cuda(), padding=0)
    img_tilde_opponent[:, 1:2, :, :] = nn.functional.conv2d(img_pad[:, 1:2, :, :], s_rg.cuda(), padding=0)
    img_tilde_opponent[:, 2:3, :, :] = nn.functional.conv2d(img_pad[:, 2:3, :, :], s_by.cuda(), padding=0)

    # Transform to linear RGB for clamp
    img_tilde_linear_rgb = color_space_transform(img_tilde_opponent, 'ycxcz2linrgb')

    # Clamp to RGB box
    return torch.clamp(img_tilde_linear_rgb, 0.0, 1.0)


def hunt_adjustment(img):
    """
    Applies Hunt-adjustment to an image

    :param img: image tensor to adjust (with NxCxHxW layout in the L*a*b* color space)
    :return: Hunt-adjusted image tensor (with NxCxHxW layout in the Hunt-adjusted L*A*B* color space)
    """
    # Extract luminance component
    L = img[:, 0:1, :, :]

    # Apply Hunt adjustment
    img_h = torch.zeros(img.size(), device='cuda')
    img_h[:, 0:1, :, :] = L
    img_h[:, 1:2, :, :] = torch.mul((0.01 * L), img[:, 1:2, :, :])
    img_h[:, 2:3, :, :] = torch.mul((0.01 * L), img[:, 2:3, :, :])

    return img_h


def hyab(reference, test, eps):
    """
    Computes the HyAB distance between reference and test images

    :param reference: reference image tensor (with NxCxHxW layout in the standard or Hunt-adjusted L*A*B* color space)
    :param test: test image tensor (with NxCxHxW layout in the standard or Hunt-adjusted L*a*b* color space)
    :param eps: float containing a small value used to improve training stability
    :return: image tensor (with Nx1xHxW layout) containing the per-pixel HyAB distances between reference and test images
    """
    delta = reference - test
    root = torch.sqrt(torch.clamp(torch.pow(delta[:, 0:1, :, :], 2), min=eps))
    delta_norm = torch.norm(delta[:, 1:3, :, :], dim=1, keepdim=True)
    return root + delta_norm  # alternative abs to stabilize training


def redistribute_errors(power_deltaE_hyab, cmax, pc, pt):
    """
    Redistributes exponentiated HyAB errors to the [0,1] range

    :param power_deltaE_hyab: float tensor (with Nx1xHxW layout) containing the exponentiated HyAb distance
    :param cmax: float containing the exponentiated, maximum HyAB difference between two colors in Hunt-adjusted L*A*B* space
    :param pc: float containing the cmax multiplier p_c (see FLIP paper)
    :param pt: float containing the target value, p_t, for p_c * cmax (see FLIP paper)
    :return: image tensor (with Nx1xHxW layout) containing redistributed per-pixel HyAB distances (in range [0,1])
    """
    # Re-map error to 0-1 range. Values between 0 and
    # pccmax are mapped to the range [0, pt],
    # while the rest are mapped to the range (pt, 1]
    deltaE_c = torch.zeros(power_deltaE_hyab.size(), device='cuda')
    pccmax = pc * cmax
    deltaE_c = torch.where(power_deltaE_hyab < pccmax, (pt / pccmax) * power_deltaE_hyab, pt + ((power_deltaE_hyab - pccmax) / (cmax - pccmax)) * (1.0 - pt))

    return deltaE_c


def feature_detection(img_y, pixels_per_degree, feature_type):
    """
    Detects edges and points (features) in the achromatic image

    :param imgy: achromatic image tensor (with Nx1xHxW layout, containing normalized Y-values from YCxCz)
    :param pixels_per_degree: float describing the number of pixels per degree of visual angle of the observer
    :param feature_type: string indicating the type of feature to detect
    :return: image tensor (with Nx2xHxW layout, with values in range [0,1]) containing large values where features were detected
    """
    # Set peak to trough value (2x standard deviations) of human edge
    # detection filter
    w = 0.082

    # Compute filter radius
    sd = 0.5 * w * pixels_per_degree
    radius = int(np.ceil(3 * sd))

    # Compute 2D Gaussian
    [x, y] = np.meshgrid(range(-radius, radius+1), range(-radius, radius+1))
    g = np.exp(-(x ** 2 + y ** 2) / (2 * sd * sd))

    if feature_type == 'edge':  # Edge detector
        # Compute partial derivative in x-direction
        Gx = np.multiply(-x, g)
    else:  # Point detector
        # Compute second partial derivative in x-direction
        Gx = np.multiply(x ** 2 / (sd * sd) - 1, g)

    # Normalize positive weights to sum to 1 and negative weights to sum to -1
    negative_weights_sum = -np.sum(Gx[Gx < 0])
    positive_weights_sum = np.sum(Gx[Gx > 0])
    Gx = torch.Tensor(Gx)
    Gx = torch.where(Gx < 0, Gx / negative_weights_sum, Gx / positive_weights_sum)
    Gx = Gx.unsqueeze(0).unsqueeze(0).cuda()

    # Detect features
    featuresX = nn.functional.conv2d(nn.functional.pad(img_y, (radius, radius, radius, radius), mode='replicate'), Gx, padding=0)
    featuresY = nn.functional.conv2d(nn.functional.pad(img_y, (radius, radius, radius, radius), mode='replicate'), torch.transpose(Gx, 2, 3), padding=0)
    return torch.cat((featuresX, featuresY), dim=1)


def color_space_transform(input_color, fromSpace2toSpace):
    """
    Transforms inputs between different color spaces

    :param input_color: tensor of colors to transform (with NxCxHxW layout)
    :param fromSpace2toSpace: string describing transform
    :return: transformed tensor (with NxCxHxW layout)
    """
    dim = input_color.size()

    # Assume D65 standard illuminant
    reference_illuminant = torch.tensor([[[0.950428545]], [[1.000000000]], [[1.088900371]]]).cuda()
    inv_reference_illuminant = torch.tensor([[[1.052156925]], [[1.000000000]], [[0.918357670]]]).cuda()

    if fromSpace2toSpace == "srgb2linrgb":
        limit = 0.04045
        transformed_color = torch.where(
            input_color > limit,
            torch.pow((torch.clamp(input_color, min=limit) + 0.055) / 1.055, 2.4),
            input_color / 12.92
        )  # clamp to stabilize training

    elif fromSpace2toSpace == "linrgb2srgb":
        limit = 0.0031308
        transformed_color = torch.where(
            input_color > limit,
            1.055 * torch.pow(torch.clamp(input_color, min=limit), (1.0 / 2.4)) - 0.055,
            12.92 * input_color
        )

    elif fromSpace2toSpace in ["linrgb2xyz", "xyz2linrgb"]:
        # Source: https://www.image-engineering.de/library/technotes/958-how-to-convert-between-srgb-and-ciexyz
        # Assumes D65 standard illuminant
        if fromSpace2toSpace == "linrgb2xyz":
            a11 = 10135552 / 24577794
            a12 = 8788810  / 24577794
            a13 = 4435075  / 24577794
            a21 = 2613072  / 12288897
            a22 = 8788810  / 12288897
            a23 = 887015   / 12288897
            a31 = 1425312  / 73733382
            a32 = 8788810  / 73733382
            a33 = 70074185 / 73733382
        else:
            # Constants found by taking the inverse of the matrix
            # defined by the constants for linrgb2xyz
            a11 = 3.241003275
            a12 = -1.537398934
            a13 = -0.498615861
            a21 = -0.969224334
            a22 = 1.875930071
            a23 = 0.041554224
            a31 = 0.055639423
            a32 = -0.204011202
            a33 = 1.057148933
        A = torch.Tensor([[a11, a12, a13],
                          [a21, a22, a23],
                          [a31, a32, a33]])

        input_color = input_color.view(dim[0], dim[1], dim[2]*dim[3]).cuda()  # NC(HW)

        transformed_color = torch.matmul(A.cuda(), input_color)
        transformed_color = transformed_color.view(dim[0], dim[1], dim[2], dim[3])

    elif fromSpace2toSpace == "xyz2ycxcz":
        input_color = torch.mul(input_color, inv_reference_illuminant)
        y = 116 * input_color[:, 1:2, :, :] - 16
        cx = 500 * (input_color[:, 0:1, :, :] - input_color[:, 1:2, :, :])
        cz = 200 * (input_color[:, 1:2, :, :] - input_color[:, 2:3, :, :])
        transformed_color = torch.cat((y, cx, cz), 1)

    elif fromSpace2toSpace == "ycxcz2xyz":
        y = (input_color[:, 0:1, :, :] + 16) / 116
        cx = input_color[:, 1:2, :, :] / 500
        cz = input_color[:, 2:3, :, :] / 200

        x = y + cx
        z = y - cz
        transformed_color = torch.cat((x, y, z), 1)

        transformed_color = torch.mul(transformed_color, reference_illuminant)

    elif fromSpace2toSpace == "xyz2lab":
        input_color = torch.mul(input_color, inv_reference_illuminant)
        delta = 6 / 29
        delta_square = delta * delta
        delta_cube = delta * delta_square
        factor = 1 / (3 * delta_square)

        clamped_term = torch.pow(torch.clamp(input_color, min=delta_cube), 1.0 / 3.0).to(dtype=input_color.dtype)
        div = (factor * input_color + (4 / 29)).to(dtype=input_color.dtype)
        input_color = torch.where(input_color > delta_cube, clamped_term, div)  # clamp to stabilize training

        L = 116 * input_color[:, 1:2, :, :] - 16
        a = 500 * (input_color[:, 0:1, :, :] - input_color[:, 1:2, :, :])
        b = 200 * (input_color[:, 1:2, :, :] - input_color[:, 2:3, :, :])

        transformed_color = torch.cat((L, a, b), 1)

    elif fromSpace2toSpace == "lab2xyz":
        y = (input_color[:, 0:1, :, :] + 16) / 116
        a = input_color[:, 1:2, :, :] / 500
        b = input_color[:, 2:3, :, :] / 200

        x = y + a
        z = y - b

        xyz = torch.cat((x, y, z), 1)
        delta = 6 / 29
        delta_square = delta * delta
        factor = 3 * delta_square
        xyz = torch.where(xyz > delta, torch.pow(xyz, 3), factor * (xyz - 4 / 29))

        transformed_color = torch.mul(xyz, reference_illuminant)

    elif fromSpace2toSpace == "srgb2xyz":
        transformed_color = color_space_transform(input_color, 'srgb2linrgb')
        transformed_color = color_space_transform(transformed_color, 'linrgb2xyz')
    elif fromSpace2toSpace == "srgb2ycxcz":
        transformed_color = color_space_transform(input_color, 'srgb2linrgb')
        transformed_color = color_space_transform(transformed_color, 'linrgb2xyz')
        transformed_color = color_space_transform(transformed_color, 'xyz2ycxcz')
    elif fromSpace2toSpace == "linrgb2ycxcz":
        transformed_color = color_space_transform(input_color, 'linrgb2xyz')
        transformed_color = color_space_transform(transformed_color, 'xyz2ycxcz')
    elif fromSpace2toSpace == "srgb2lab":
        transformed_color = color_space_transform(input_color, 'srgb2linrgb')
        transformed_color = color_space_transform(transformed_color, 'linrgb2xyz')
        transformed_color = color_space_transform(transformed_color, 'xyz2lab')
    elif fromSpace2toSpace == "linrgb2lab":
        transformed_color = color_space_transform(input_color, 'linrgb2xyz')
        transformed_color = color_space_transform(transformed_color, 'xyz2lab')
    elif fromSpace2toSpace == "ycxcz2linrgb":
        transformed_color = color_space_transform(input_color, 'ycxcz2xyz')
        transformed_color = color_space_transform(transformed_color, 'xyz2linrgb')
    elif fromSpace2toSpace == "lab2srgb":
        transformed_color = color_space_transform(input_color, 'lab2xyz')
        transformed_color = color_space_transform(transformed_color, 'xyz2linrgb')
        transformed_color = color_space_transform(transformed_color, 'linrgb2srgb')
    elif fromSpace2toSpace == "ycxcz2lab":
        transformed_color = color_space_transform(input_color, 'ycxcz2xyz')
        transformed_color = color_space_transform(transformed_color, 'xyz2lab')
    else:
        sys.exit('Error: The color transform %s is not defined!' % fromSpace2toSpace)

    return transformed_color

class BCEDiceLoss(nn.Module):
    def __init__(self):
        super().__init__()

    def forward(self, input, target):
        bce = F.binary_cross_entropy_with_logits(input, target)
        smooth = 1e-5
        input = torch.sigmoid(input)
        num = target.size(0)
        input = input.view(num, -1)
        target = target.view(num, -1)
        intersection = (input * target)
        dice = (2. * intersection.sum(1) + smooth) / (input.sum(1) + target.sum(1) + smooth)
        dice = 1 - dice.sum() / num
        return 0.5 * bce + dice

class LPIPS(nn.Module):
    def __init__(self):
        super().__init__()
        self.loss_lpips = lpips.LPIPS(net="alex")
        self.loss_lpips = self.loss_lpips.cuda()
        
    def forward(self, input, target):
        loss = self.loss_lpips(input, target)
        loss = loss.mean()
        
        if torch.isnan(loss):
            is_nan = True
            loss = 1
        else:
            is_nan = False
        return loss, is_nan

# class LPIPS(nn.Module):
#     def __init__(self):
#         super().__init__()
#         self.loss_lpips = lpips.LPIPS(net="alex")
#         self.loss_lpips = self.loss_lpips.cuda()
        
#     def forward(self, input, target):
#         loss = self.loss_lpips(input, target)
#         loss = loss.mean()
        
        
#         return loss
    
class LPIPS_regularized(nn.Module):
    def __init__(self):
        super().__init__()
        self.loss_lpips = lpips.LPIPS(net='alex', spatial = 'True')
        self.loss_lpips = self.loss_lpips.cuda()
        self.loss_L1 = nn.L1Loss(reduction = "none")
        self.loss_L1 = self.loss_L1.cuda()
        self.loss_L2 = nn.MSELoss(reduction = "none")
        self.loss_L2 = self.loss_L2.cuda()
        
    def forward(self, input, target, reg_type = 'L1'):
        loss_lpips = self.loss_lpips(input, target)
        if reg_type == "L1":
            regularizer = self.loss_L1(input, target)
        elif reg_type == "L2":
            regularizer = self.loss_L2(input, target)
        
        loss_lpips_regularized = loss_lpips * regularizer
        
        return loss_lpips_regularized

class LovaszHingeLoss(nn.Module):
    def __init__(self):
        super().__init__()

    def forward(self, input, target):
        input = input.squeeze(1)
        target = target.squeeze(1)
        loss = lovasz_hinge(input, target, per_image=True)

        return loss
    
class MSELoss(nn.Module):
    def __init__(self):
        super().__init__()
        
    def forward(self, input, target):
        loss_type = nn.MSELoss(reduction="sum")
        loss = loss_type(input, target)
        
        return loss

# class MSELossLPIPS(nn.Module):
#     def __init__(self):
#         super().__init__()
#         self.loss_lpips = lpips.LPIPS(net='vgg')
#         self.loss_lpips = self.loss_lpips.cuda()
#     def forward(self, input, target, weight_MSE=1, weight_LPIPS=255):
#         loss_mse = nn.MSELoss(reduction="mean")
 
#         loss1 = loss_mse(input, target)
        
#         input = torch.squeeze(input)
#         target = torch.squeeze(target)
#         target = target.clamp(-1., 1.)
#         input = input.clamp(-1., 1.)
#         # input = input.cpu()
#         # target = target.to("cuda")
#         loss2 = self.loss_lpips(input, target)
        
#         return weight_MSE*loss1, weight_LPIPS*loss2
    
class Loss3(nn.Module):
    def __init__(self):
        super().__init__()
        self.loss_lpips = lpips.LPIPS(net='vgg')
        self.loss_lpips = self.loss_lpips.cuda()
        self.loss_L1 = nn.L1Loss(reduction = "mean")

        
    def forward(self, input, target, weight_l1=0.7, weight_LPIPS=0.3): 
        loss1 = self.loss_L1(input, target)
        loss4 = self.loss_lpips(input, target)
        loss4 = loss4.mean()
        return weight_l1*loss1, weight_LPIPS*loss4
    
class Loss5(nn.Module):
    def __init__(self):
        super().__init__()
        self.loss_lpips = lpips.LPIPS(net='vgg')
        self.loss_lpips = self.loss_lpips.cuda()

    def forward(self, input, target):
        input = torch.squeeze(input)
        target = torch.squeeze(target)
        input = torch.unsqueeze(input,0)
        input = torch.unsqueeze(input,0)
        target = torch.unsqueeze(target,0)          
        target = torch.unsqueeze(target,0)  
        loss = self.loss_lpips(input, target)
        
        return loss

def CenterCrop(tensor,ccsize=224):
    """
    

    Parameters
    ----------
    tensor : A 4-Dimensional pytorch tensor, shaped [BS, C, H, W]
    ccsize : Type: positive integer
        DESCRIPTION. The default is 224. Size of the square shaped window. 

    Returns
    -------
    tensor : 4-Dimensional pytorch tensor, shaper [BS, C, ccsize, ccsize], 
    extracted from the input parameter 'tensor'. It will be the center-cropped 
    tensor.

    """
    bs,c,h,w = tensor.shape
    h0 = ceil(h/2)-1 - ceil(ccsize/2)
    h1 = ceil(h/2)-1 + ceil(ccsize/2)
    w0 = ceil(w/2)-1 - ceil(ccsize/2)
    w1 = ceil(w/2)-1 + ceil(ccsize/2)
    tensor = tensor[:,:,h0:h1,w0:w1]
    return tensor

class VGGWPL(torch.nn.Module):
    #source:https://gist.github.com/brucemuller/37906a86526f53ec7f50af4e77d025c9
    def __init__(self, resize=True):
        super(VGGWPL, self).__init__()
        blocks = []
        blocks.append(torchvision.models.vgg16(pretrained=True).features[:4].eval())
        blocks.append(torchvision.models.vgg16(pretrained=True).features[4:9].eval())
        blocks.append(torchvision.models.vgg16(pretrained=True).features[9:16].eval())
        blocks.append(torchvision.models.vgg16(pretrained=True).features[16:23].eval())
        blocks.append(torchvision.models.vgg16(pretrained=True).features[23:30].eval())
        for bl in blocks:
            for p in bl.parameters():
                p.requires_grad = False
        self.blocks = torch.nn.ModuleList(blocks)
        self.transform = torch.nn.functional.interpolate
        self.resize = resize
        self.register_buffer("mean", torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1))
        self.register_buffer("std", torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1))

    def forward(self, input, target, feature_layers=[0], style_layers=[]):
        #input and target shapes are [BS, C, H, W], with C being 1 for DCCR-Net
        
        bs, c, h, w =  input.shape
        p_size = 224
        if input.shape[1] != 3:
            input = input.repeat(1, 3, 1, 1)
            target = target.repeat(1, 3, 1, 1)
        input = (input-self.mean) / self.std
        target = (target-self.mean) / self.std
        if self.resize or (h,w)<(p_size,p_size):
            input = self.transform(input, mode='bilinear', size=(224, 224), align_corners=False)
            target = self.transform(target, mode='bilinear', size=(224, 224), align_corners=False)
            nstacked = 1.0
        else:
            if p_size/h < 0.7 or p_size/w < 0.7:
                npat_h = ceil(h/p_size)
                npat_w = ceil(w/p_size)
                stride_h = h-p_size
                stride_w = w-p_size
                input_patches = input.unfold(2,p_size,stride_h).unfold(3,p_size,stride_w)
                target_patches = target.unfold(2,p_size,stride_h).unfold(3,p_size,stride_w)
                nstacked = npat_h*npat_w
                input = input_patches.reshape((bs*nstacked,3,p_size,p_size))
                target = target_patches.reshape((bs*nstacked,3,p_size,p_size))
            else:
                input = CenterCrop(input)
                target = CenterCrop(target)
                nstacked = 1.0
        loss = 0.0
        x = input
        y = target
        for i, block in enumerate(self.blocks):
            x = block(x)
            y = block(y)
            if i in feature_layers:
                # if i == 0:
                #     lambda_n = torch.abs(lambda_1)
                #     loss_VGG_1 = torch.nn.functional.l1_loss(x, y)
                # elif i == 1:
                #     lambda_n = torch.abs(lambda_2)
                #     loss_VGG_2 = torch.nn.functional.l1_loss(x, y)
                # elif i == 2:
                #     lambda_n = torch.abs(lambda_3)
                #     loss_VGG_3 = torch.nn.functional.l1_loss(x, y)
                # elif i == 3:
                #     lambda_n = torch.abs(lambda_4)
                #     loss_VGG_4 = torch.nn.functional.l1_loss(x, y)
                # elif i == 4:
                #     lambda_n = torch.abs(lambda_5)
                #     loss_VGG_5 = torch.nn.functional.l1_loss(x, y)
                loss += torch.nn.functional.l1_loss(x, y)
            if i in style_layers:
                act_x = x.reshape(x.shape[0], x.shape[1], -1)
                act_y = y.reshape(y.shape[0], y.shape[1], -1)
                gram_x = act_x @ act_x.permute(0, 2, 1)
                gram_y = act_y @ act_y.permute(0, 2, 1)
                loss += torch.nn.functional.l1_loss(gram_x, gram_y)
        return loss
    
# class MCLF(nn.Module):
#     def __init__(self):
#         super().__init__()
        
#     def forward(self, input, target):
        

# class Loss3(nn.Module):
#     def __init__(self):
#         super().__init__()   
#         self.loss_L1 = nn.L1Loss(reduction = "mean")
#         self.msssim_loss = SSIM(win_size=11, win_sigma=1.5, data_range=1, size_average=True, channel=1)
        
        
#     def forward(self, input, target, weight_L1=100, weight_MSSSIM = 100, alpha = 0.84):
#         #Input was size 448x448 and target was 1x1x448x448 before this operation during training, so we set the size manually to prevent further errors
#         template1 = torch.BoolTensor(1,448,448)
#         input = input.reshape(template1.shape)
#         target = target.reshape(template1.shape)
        
#         loss1 = self.loss_L1(input, target)
#         loss4 = 1-self.msssim_loss(torch.unsqueeze(input,0),torch.unsqueeze(target,0))
#         if numpy.isnan(loss4.item()):
#             print("WARNING: NAN VALUE OF MSSSIM LOSS GENERATED")
#         loss4 = (1-alpha)*weight_MSSSIM*loss4
#         loss1 = alpha*weight_L1*loss1

#         return loss1, loss4
    
# class Loss4(nn.Module):
#     def __init__(self):
#         super().__init__()   
#         self.loss_L1 = nn.L1Loss(reduction = "mean")
#         self.msssim_loss = 1-SSIM(win_size=11, win_sigma=1.5, data_range=1, size_average=True, channel=1)
        
        
#     def forward(self, input, target, weight_L1=100, weight_MSSSIM = 100, alpha = 0.84):
#         #Input was size 448x448 and target was 1x1x448x448 before this operation during training, so we set the size manually to prevent further errors
#         template1 = torch.BoolTensor(1,448,448)
#         input = input.reshape(template1.shape)
#         target = target.reshape(template1.shape)
        
#         loss1 = self.loss_L1(input, target)
#         loss4 = self.msssim_loss(torch.unsqueeze(input,0),torch.unsqueeze(target,0))
#         if numpy.isnan(loss4.item()):
#             print("WARNING: NAN VALUE OF MSSSIM LOSS GENERATED")
#         loss4 = (1-alpha)*weight_MSSSIM*loss4
#         loss1 = alpha*weight_L1*loss1

#         return loss1, loss4
