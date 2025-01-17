import torch
import torch.nn as nn 
from Layers import EncoderLayer, DecoderLayer
from Embed import PositionalEncoder
from Sublayers import Norm
import copy
from SubSampleCNN import VGG2L
from transformers import BertModel
from transformers import BertConfig
from embedding import PositionalEncoding
from espnet_local.nets.pytorch_backend.conformer.encoder import Encoder
from espnet_local.nets.pytorch_backend.nets_utils import make_non_pad_mask

from SubSampleNet import Conv2dSubsampling


def get_clones(module, N):
    return nn.ModuleList([copy.deepcopy(module) for i in range(N)])


class audio_Encoder(nn.Module):
    def __init__(self, d_mfcc, max_mfcc_length, N, heads, dropout):
        super().__init__()
        self.N = N
        self.pe = PositionalEncoder(d_model=d_mfcc, max_seq_len=max_mfcc_length,dropout=dropout)
        self.layers = get_clones(EncoderLayer(d_model=d_mfcc, heads=heads, dropout=dropout), N)
        self.norm = Norm(d_model=d_mfcc)

    def forward(self, src, audio_mask):
        x = self.pe(src)
        for i in range(self.N):
            x = self.layers[i](x, audio_mask)
        return self.norm(x)


class text_Encoder(nn.Module):
    def __init__(self, d_text, bert_embedding_length, N, heads, dropout):
        super().__init__()
        self.N = N
        self.pe = PositionalEncoder(d_model=d_text, max_seq_len=bert_embedding_length, dropout=dropout)
        self.layers = get_clones(EncoderLayer(d_model=d_text, heads=heads, dropout=dropout), N)
        self.norm = Norm(d_model=d_text)

    def forward(self, src, text_mask):
        x = self.pe(src)
        for i in range(self.N):
            x = self.layers[i](x, text_mask)
        return self.norm(x)

class Decoder(nn.Module):
    def __init__(self, d_text, d_mfcc, N, heads, dropout, bert_embedding_length):
        super().__init__()
        self.N = N
        self.linear = nn.Linear(d_mfcc, d_text)
        self.pe = PositionalEncoder(d_model=d_text, max_seq_len=bert_embedding_length, dropout=dropout)
        self.layers = get_clones(DecoderLayer(d_text, heads, dropout), N)
        self.norm = Norm(d_text)

    def forward(self, trg, e_outputs, src_mask, trg_mask):
        e_outputs = self.linear(e_outputs)
        x = self.pe(trg)
        for i in range(self.N):
            x, attention_map = self.layers[i](x, e_outputs, src_mask, trg_mask)
        return self.norm(x), attention_map


def load_asr(opt, d):
    pretrained_model = torch.load(opt.asr_path)['model']
    enc_dic = {}
    for key in d:
        enc_dic[key] = pretrained_model["encoder." + key]
    return enc_dic


class Prosody_extrator(nn.Module):
    def __init__(self, d_mfcc, d_text, max_mfcc_length, bert_embedding_length, checkpoint, N, heads, dropout, num_tags, bs):
        super().__init__()
        self.sub_sampling = Encoder(
            idim=83,
            attention_dim=256,
            attention_heads=4,
            linear_units=2048,
            num_blocks=12,
            dropout_rate=0.1,
            positional_dropout_rate=0.1,
            attention_dropout_rate=0.0,
            activation_type="swish",
            macaron_style=1,
            use_cnn_module=1,
            cnn_module_kernel=31,
        )  #comformer-Char

        #self.sub_sampling = Conv2dSubsampling(idim=83, odim=256, dropout_rate=0.1, pos_enc=None)  #CNN-Char
        self.audio_encoder = audio_Encoder(d_mfcc=d_mfcc, max_mfcc_length=max_mfcc_length, N=N, heads=heads, dropout=dropout)
        self.config = BertConfig.from_pretrained('./../bert/config.json')
        self.bert = BertModel(self.config)
        self.cross_decoder = Decoder(d_text=d_text, d_mfcc=d_mfcc, N=N, heads=heads, dropout=dropout, bert_embedding_length=bert_embedding_length)
        self.out = nn.Linear(d_text, num_tags)
        self.mfcc_len = max_mfcc_length
        self.bs = bs

    def forward(self, mfcc, input_ids, attention_mask, mfcc_mask, src_mask):
        sampled_feature, _ = self.sub_sampling(mfcc, src_mask) #comformer-CNN
        #sampled_feature, _ = self.sub_sampling(mfcc, None)  #CNN-Char
        audio_e_out = self.audio_encoder(src=sampled_feature, audio_mask=None)
        embeddings = self.bert(input_ids=input_ids, attention_mask=attention_mask ,output_hidden_states=True).last_hidden_state  #  (B, 512, 768)
        cross_out, attention_map = self.cross_decoder(embeddings, audio_e_out, None, None)
        pred = self.out(cross_out)
        return pred, attention_map#(B，512，2688)

    
def get_model(opt):
    
    assert opt.d_model % opt.heads == 0
    assert opt.dropout < 1

    model = Prosody_extrator(d_mfcc=opt.d_mfcc, d_text=opt.d_text, max_mfcc_length=opt.max_mfcc_length, bert_embedding_length=opt.bert_embedding_length, checkpoint=opt.bert_checkpoint, N=opt.n_layers, heads=opt.heads, dropout=opt.dropout, num_tags=opt.num_tags, bs=opt.batchsize)

    return model
