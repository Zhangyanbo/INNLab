import torch
import torch.nn as nn
from torch.nn.utils import spectral_norm
import torch.nn.functional as F
from INN.SpectralNormGouk import spectral_norm as spectral_norm_g
import INN.INNAbstract as INNAbstract
import numpy as np

# compute v.Jacobian, source: https://github.com/jarrelscy/iResnet
def vjp(ys, xs, v):
    vJ = torch.autograd.grad(ys, xs, grad_outputs=v, create_graph=True, retain_graph=True, allow_unused=True)
    return tuple([j for j in vJ])


class SNFCN(nn.Module):
    '''
    spectral normalized fully connected function
    '''
    def __init__(self, dim, w=8, k=0.8, batch_norm=False):
        super(SNFCN, self).__init__()
        self.k = k
        self.dim = dim
        self.g = nn.Sequential(spectral_norm(nn.Linear(dim, w*dim)), nn.GELU(),
                                spectral_norm(nn.Linear(w*dim, w*dim)), nn.GELU(),
                                spectral_norm(nn.Linear(w*dim, dim))
                                )
        
        self._initialize()
    
    def _initialize(self):
        self.forward(torch.randn((2, self.dim))) # doing one compuatation to initialize the spectral_norm
        return
    
    def forward(self, x):
        x = self.g(self.k * x)
        
        return x

class iResNet(INNAbstract.iResNetModule):
    '''
    i-ResNet which g is a fully connected network
    '''
    def __init__(self, dim, g=None, beta=0.8, w=8, num_iter=1, num_n=10):
        '''
        beta: the Lip constant, beta < 1
        w: the width of the hidden layer
        '''
        super(iResNet, self).__init__()
        
        self.dim = dim
        self.num_iter = num_iter
        self.num_n = num_n
        
        if g is None:
            self.net = SNFCN(dim, w=w, k=beta)
        else:
            self.net = g
    
    def g(self, x):
        return self.net(x)
    
    def logdet(self, x, g):
        self.eval()
        logdet = 0
        for i in range(self.num_iter):
            v = torch.randn(x.shape) # random noise
            v = v.to(x.device)
            w = v
            for k in range(1, self.num_n):
                w = vjp(g, x, w)[0]
                logdet += (-1)**(k+1) * torch.sum(w * v, dim=-1) / k
        
        logdet /= self.num_iter
        self.train()
        return logdet


class SNCov1d(nn.Module):
    '''
    Spectrum Normalized 1-d Conv Layer stack
    '''
    def __init__(self, channel, kernel_size, w=8, k=0.8):
        super(SNCov1d, self).__init__()
        if kernel_size % 2 != 1:
            raise Exception(f'The kernel_size must be an odd number, but got {kernel_size}.')
        
        padding = (kernel_size - 1) // 2
        self.channel = channel
        self.kernel_size = kernel_size
        self.k = k
        
        self.net = nn.Sequential(spectral_norm_g(nn.Conv1d(channel, w * channel, kernel_size=kernel_size, padding=padding)),
                                 nn.GELU(),
                                 spectral_norm_g(nn.Conv1d(w * channel, w * channel, kernel_size=kernel_size, padding=padding)),
                                 nn.GELU(),
                                 spectral_norm_g(nn.Conv1d(w * channel, channel, kernel_size=kernel_size, padding=padding))
                                )
        
        self._initialize()
    
    def _initialize(self):
        self.forward(torch.randn((2, self.channel, self.kernel_size))) # doing one compuatation to initialize the spectral_norm
        return
    
    def forward(self, x):
        x = self.net(self.k * x)
        return x


class SNCov2d(nn.Module):
    '''
    Spectrum Normalized 1-d Conv Layer stack
    '''
    def __init__(self, channel, kernel_size, w=8, k=0.8):
        super(SNCov2d, self).__init__()
        if kernel_size % 2 != 1:
            raise Exception(f'The kernel_size must be an odd number, but got {kernel_size}.')
        
        padding = (kernel_size - 1) // 2
        self.channel = channel
        self.kernel_size = kernel_size
        self.k = k
        
        self.net = nn.Sequential(spectral_norm_g(nn.Conv2d(channel, w * channel, kernel_size=kernel_size, padding=padding)),
                                 nn.GELU(),
                                 spectral_norm_g(nn.Conv2d(w * channel, w * channel, kernel_size=kernel_size, padding=padding)),
                                 nn.GELU(),
                                 spectral_norm_g(nn.Conv2d(w * channel, channel, kernel_size=kernel_size, padding=padding))
                                )
        
        self._initialize()
    
    def _initialize(self):
        self.forward(torch.randn((2, self.channel, self.kernel_size, self.kernel_size))) # doing one compuatation to initialize the spectral_norm
        return
    
    def forward(self, x):
        x = self.net(self.k * x)
        return x


class NormalDistribution(INNAbstract.Distribution):
    '''
    Generate normal distribution and compute log probablity
    '''
    def __init__(self):
        super(NormalDistribution, self).__init__()
    
    def logp(self, x):
        logps = -1 * (x ** 2)

        if len(x.shape) == 1:
            # linear layer
            raise Exception(f'The input must have a batch dimension, but got dim={x.shape}.')
        if len(x.shape) == 2:
            # [batch, dim]
            return logps.sum(dim=-1)
        if len(x.shape) == 3:
            # [batch, channel, dim_1d], 1d conv
            return logps.reshape(x.shape[0], -1).sum(dim=-1)
        if len(x.shape) == 4:
            # [batch, channel, dim_x, dim_y], 2d conv
            return logps.reshape(x.shape[0], -1).sum(dim=-1)
        
        raise Exception(f'The input dimension should be 1,2,3, or 4, but got {len(x.shape)}.')
    
    def sample(self, shape):
        return torch.randn(shape)

class LaplaceDistribution(INNAbstract.Distribution):
    '''
    Generate normal distribution and compute log probablity
    '''
    def __init__(self, mu=0, beta=1.0):
        super(LaplaceDistribution, self).__init__()
        self.beta = beta
        self.A = np.log(2 * beta)
        self.dist = torch.distributions.laplace.Laplace(mu, beta)
    
    def logp(self, x):
        logps = self.dist.log_prob(x)

        if len(x.shape) == 1:
            # linear layer
            raise Exception(f'The input must have a batch dimension, but got dim={x.shape}.')
        if len(x.shape) == 2:
            # [batch, dim]
            return logps.sum(dim=-1)
        if len(x.shape) == 3:
            # [batch, channel, dim_1d], 1d conv
            return logps.reshape(x.shape[0], -1).sum(dim=-1)
        if len(x.shape) == 4:
            # [batch, channel, dim_x, dim_y], 2d conv
            return logps.reshape(x.shape[0], -1).sum(dim=-1)
        
        raise Exception(f'The input dimension should be 1,2,3, or 4, but got {len(x.shape)}.')
    
    def sample(self, shape):
        return self.dist.sample(shape)



def permutation_matrix(dim):
    # generate a permuation matrix
    x = torch.zeros((dim, dim))
    for i in range(dim):
        x[i, (i+1) % (dim)] = 1
    return x

class PLUMatrix(nn.Module):
    '''
    PLU decomposition for invertible matrix
    ref: https://arxiv.org/pdf/1807.03039.pdf section 3.2
    '''
    def __init__(self, dim, positive_s=False, eps=1e-8):
        super(PLUMatrix, self).__init__()

        self.positive_s = positive_s
        self.eps = eps
        self._initialize(dim)

    def _initialize(self, dim):
        w, P, L, U = self.sampling_W(dim)
        self.P = P
        self._L = nn.Parameter(L)
        self._U = nn.Parameter(torch.triu(U, diagonal=1))
        if self.positive_s:
            self.log_s = nn.Parameter(torch.log(torch.abs(torch.diag(U))))
        else:
            self.log_s = nn.Parameter(torch.diag(U))

        self.I = torch.diag(torch.ones(dim))
        return
    
    def sampling_W(self, dim):
        # sample a rotation matrix
        W = torch.empty(dim, dim)
        torch.nn.init.orthogonal_(W)
        # compute LU
        LU, pivot = torch.lu(W)
        P, L, U = torch.lu_unpack(LU, pivot)
        return W, P, L, U

    def L(self):
        # turn l to lower
        l_ = torch.tril(self._L, diagonal=-1)

        return l_ + self.I.to(self._L.device)
    
    def U(self):
        return torch.triu(self._U, diagonal=1)
    
    def W(self):
        if self.positive_s:
            s = torch.diag(torch.exp(self.log_s))
        else:
            s = torch.diag(self.log_s)
        return self.P.to(self._L.device) @ self.L() @ (self.U() + s)
    
    def inv_W(self):
        # need to be optimized based on the LU decomposition
        w = self.W()
        inv_w = torch.inverse(w)
        return inv_w
    
    def logdet(self):
        if self.positive_s:
            return torch.sum(self.log_s)
        else:
            return torch.sum(torch.log(torch.abs(self.log_s) + self.eps))

class InvertibleLinear(INNAbstract.INNModule):
    '''
    Invertible Linear
    ref: https://arxiv.org/pdf/1807.03039.pdf section 3.2
    '''
    def __init__(self, dim, positive_s=False, eps=1e-8):
        super(InvertibleLinear, self).__init__()
        self.mat = PLUMatrix(dim, positive_s=positive_s, eps=eps)
    
    def logdet(self, x):
        return self.mat.logdet().repeat(x.shape[0])

    def forward(self, x):
        weight = self.mat.W()
        return F.linear(x, weight)
    
    def inverse(self, y):
        return F.linear(y, self.mat.inv_W())


class real_nvp_element(INNAbstract.INNModule):
    '''
    The very basic element of real nvp
    '''
    def __init__(self, dim, f_log_s, f_t, mask=None, eps=1e-8, clip=None):
        super(real_nvp_element, self).__init__()

        if mask is None:
            self.mask = self.generate_mask(dim)
        else:
            self.mask = mask
        
        self.f_log_s = f_log_s
        self.f_t = f_t
        self.eps = eps
        self.clip = clip
    
    def generate_mask(self, dim):
        '''
        generate mask for given dimension number `dim`
        '''
        mask = torch.zeros((1, dim))
        for i in range(dim):
            if i % 2 == 0:
                mask[0, i] = 1
        return mask
    
    def get_s(self, x):
        if len(x.shape) == 1:
            b = self.mask.squeeze().to(x.device)
        else:
            b = self.mask.to(x.device)
        
        log_s = self.f_log_s(b * x)

        if self.clip is not None:
            # clip the log(s), to avoid extremely large numbers
            log_s = self.clip * torch.tanh(log_s / self.clip)
        
        s = torch.exp(log_s)
        return s, log_s

    def forward(self, x):
        if len(x.shape) == 1:
            b = self.mask.squeeze().to(x.device)
        else:
            b = self.mask.to(x.device)
        
        s, log_s = self.get_s(b * x)

        log_det_J = torch.sum(log_s * (1-b), dim=-1)

        t = self.f_t(b * x)

        y = b * x + (1 - b) * (x * s + t)

        return y, log_det_J
    
    def inverse(self, y):
        if len(y.shape) == 1:
            b = self.mask.squeeze().to(y.device)
        else:
            b = self.mask.to(y.device)
        
        s, log_s = self.get_s(b * y)

        t = self.f_t(b * y)

        x = b * y + (1 - b) * (y - t) / (s + self.eps)

        return x

def generate_mask(dim):
    '''
    generate mask for given dimension number `dim`
    '''
    mask = torch.zeros((1, dim))
    for i in range(dim):
        if i % 2 == 0:
            mask[0, i] = 1
    return mask

class combined_real_nvp(INNAbstract.INNModule):
    '''
    The very basic element of real nvp
    '''
    def __init__(self, dim, f_log_s, f_t, mask=None, clip=None):
        super(combined_real_nvp, self).__init__()

        if mask is None:
            self.mask = generate_mask(dim)
        else:
            self.mask = mask
        
        self.nvp_1 = real_nvp_element(dim, f_log_s, f_t, mask=self.mask, clip=clip)
        self.nvp_2 = real_nvp_element(dim, f_log_s, f_t, mask=1 - self.mask, clip=clip)
    
    def generate_mask(self, dim):
        '''
        generate mask for given dimension number `dim`
        '''
        mask = torch.zeros((1, dim))
        for i in range(dim):
            if i % 2 == 0:
                mask[0, i] = 1
        return mask

    def forward(self, x):
        x, log_det_J_1 = self.nvp_1(x)
        x, log_det_J_2 = self.nvp_2(x)

        return x, log_det_J_1 + log_det_J_2
    
    def inverse(self, y):
        y = self.nvp_2.inverse(y)
        y = self.nvp_1.inverse(y)

        return y


class NICE(INNAbstract.INNModule):
    '''
    dim: dimension of input / output
    m: function m
    '''
    def __init__(self, dim, m, mask=None):
        super(NICE, self).__init__()

        if mask is None:
            self.mask = generate_mask(dim)
        else:
            self.mask = mask
        self.m = m
    
    def forward(self, x):
        if len(x.shape) == 1:
            b = self.mask.squeeze().to(x.device)
        else:
            b = self.mask.to(x.device)
        
        x = x + (1-b) * self.m(b * x)
        x = x + b * self.m((1-b) * x)
        return x
    
    def logdet(self):
        return 0
    
    def inverse(self, y):
        if len(y.shape) == 1:
            b = self.mask.squeeze().to(y.device)
        else:
            b = self.mask.to(y.device)
        y = y - b * self.m((1-b) * y)
        y = y - (1 - b) * self.m(b * y)
        return y


class default_net(nn.Module):
    def __init__(self, dim, k, activation_fn=None):
        super(default_net, self).__init__()
        self.activation_fn = activation_fn
        self.net = self.default_net(dim, k, activation_fn)
    
    def default_net(self, dim, k, activation_fn):
        if activation_fn == None:
            ac = nn.SELU#nn.LeakyReLU
        else:
            ac = activation_fn
        
        block = nn.Sequential(nn.Linear(dim, k * dim), ac(),
                              nn.Linear(k * dim, k * dim), ac(),
                              nn.Linear(k * dim, dim))
        block.apply(self.init_weights)
        return block
    
    def init_weights(self, m):
        nonlinearity = 'leaky_relu' # set to leaky_relu by default

        if self.activation_fn is nn.LeakyReLU:
            nonlinearity = 'leaky_relu'
        if self.activation_fn is nn.ReLU:
            nonlinearity = 'relu'
        if self.activation_fn is nn.SELU:
            nonlinearity = 'selu'
        if self.activation_fn is nn.Tanh:
            nonlinearity = 'tanh'
        if self.activation_fn is nn.Sigmoid:
            nonlinearity = 'sigmoid'
        
        if type(m) == nn.Linear:
            # doing xavier initialization
            # NOTE: Kaiming initialization will make the output too high, which leads to nan
            torch.nn.init.xavier_uniform_(m.weight.data)
            torch.nn.init.zeros_(m.bias.data)
    
    def forward(self, x):
        return self.net(x)


class reshape(nn.Module):
    '''
    Invertible reshape
    
    * shape_in: shape of the input. Note that batch_size don't need to be included.
    * shape_out: shape of the output
    '''
    def __init__(self, shape_in, shape_out):
        super(reshape, self).__init__()
        
        #self._check_shape(shape_in, shape_out)
        self.shape_in = shape_in
        self.shape_out = shape_out
    
    def _check_shape(self, shape_in, shape_out):
        '''
        Check if the in and out are in the same size
        '''
        s_in = 1
        for d in shape_in:
            s_in *= d
        s_out = 1
        for d in shape_out:
            s_out *= d
        
        if s_in != s_out:
            raise ValueError(f'shape_in and shape_out must have the same size, but got {s_in} and {s_out}.')
        return
    
    def forward(self, x):
        batch_size = x.shape[0]
        return x.reshape(batch_size, *self.shape_out)
    
    def inverse(self, x):
        batch_size = x.shape[0]
        return x.reshape(batch_size, *self.shape_in)


class _MuVar(nn.Module):
    r'''
    Abstract class of MuVar
    '''
    def __init__(self, feature_in, feature_out, eps=1e-8):
        super(_MuVar, self).__init__()
        self.feature_in = feature_in
        self.feature_out = feature_out
        self.feature_y = feature_out
        self.feature_z = feature_in - feature_out
        self.eps = eps
    
    def _initialize_weights(self):
        raise NotImplementedError('initialization not implemented!')
    
    def exp_var(self, log_var):
        return self.eps + torch.exp(log_var)


class MuVarVector(_MuVar):
    def __init__(self, feature_in, feature_out):
        super(MuVarVector, self).__init__(feature_in, feature_out)
        self.linear_mu = nn.Linear(self.feature_y, self.feature_z)
        self.linear_log_var = nn.Linear(self.feature_y, self.feature_z)
        self._initialize_weights()
    
    def _initialize_weights(self):
        nn.init.zeros_(self.linear_mu.weight)
        nn.init.zeros_(self.linear_mu.bias)
        nn.init.zeros_(self.linear_log_var.weight)
        nn.init.zeros_(self.linear_log_var.bias)

    def forward(self, y):
        mu = self.linear_mu(y)
        var = self.exp_var(self.linear_log_var(y))
        log_det = -1 * torch.log(var).sum(dim=-1)
        return mu, var, log_det


class MuVar1d(_MuVar):
    def __init__(self, feature_in, feature_out):
        super(MuVar1d, self).__init__(feature_in, feature_out)
        self.conv_mu = nn.Conv1d(in_channels=self.feature_y, out_channels=self.feature_z, kernel_size=3, padding=1)
        self.conv_log_var = nn.Conv1d(in_channels=self.feature_y, out_channels=self.feature_z, kernel_size=3, padding=1)
        self._initialize_weights()
    
    def _initialize_weights(self):
        nn.init.zeros_(self.conv_mu.weight)
        nn.init.zeros_(self.conv_mu.bias)
        nn.init.zeros_(self.conv_log_var.weight)
        nn.init.zeros_(self.conv_log_var.bias)
    
    def forward(self, y):
        mu = self.conv_mu(y)
        var = self.exp_var(self.conv_log_var(y))
        batch = var.shape[0]
        log_det = -1 * torch.log(var).reshape(batch, -1).sum(dim=-1)
        return mu, var, log_det


class MuVar2d(_MuVar):
    def __init__(self, feature_in, feature_out):
        super(MuVar2d, self).__init__(feature_in, feature_out)
        self.conv_mu = nn.Conv2d(in_channels=self.feature_y, out_channels=self.feature_z, kernel_size=3, padding=1)
        self.conv_log_var = nn.Conv2d(in_channels=self.feature_y, out_channels=self.feature_z, kernel_size=3, padding=1)
        self._initialize_weights()
    
    def _initialize_weights(self):
        nn.init.zeros_(self.conv_mu.weight)
        nn.init.zeros_(self.conv_mu.bias)
        nn.init.zeros_(self.conv_log_var.weight)
        nn.init.zeros_(self.conv_log_var.bias)
    
    def forward(self, y):
        mu = self.conv_mu(y)
        var = self.exp_var(self.conv_log_var(y))
        batch = var.shape[0]
        log_det = -1 * torch.log(var).reshape(batch, -1).sum(dim=-1)
        return mu, var, log_det


class MuVar(nn.Module):
    r'''
    Estimate mean and var when doing feature rescale.
    '''
    def __init__(self, feature_in, feature_out):
        super(MuVar, self).__init__()
        self.initialized = False
        self.feature_in = feature_in
        self.feature_out = feature_out
    
    def _initialize(self, y):
        # initialize mu and var based on shape of y
        batch, *shape = y.shape
        num_features = shape[0]
        self.initialized = True
        if len(shape) == 1:
            self.mu_var = MuVarVector(self.feature_in, self.feature_out)
            return
        if len(shape) == 2:
            self.mu_var = MuVar1d(self.feature_in, self.feature_out)
            return
        if len(shape) == 3:
            self.mu_var = MuVar2d(self.feature_in, self.feature_out)
            return
    
    def forward(self, y):
        if not self.initialized:
            self._initialize(y)
        
        return self.mu_var(y)