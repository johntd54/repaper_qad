#@title
import gzip
import html
import os
from functools import lru_cache
import torch

import ftfy
import regex as re
import numpy as np

@lru_cache()
def bytes_to_unicode():
    """
    Returns list of utf-8 byte and a corresponding list of unicode strings.
    The reversible bpe codes work on unicode strings.
    This means you need a large # of unicode characters in your vocab if you want to avoid UNKs.
    When you're at something like a 10B token dataset you end up needing around 5K for decent coverage.
    This is a signficant percentage of your normal, say, 32K bpe vocab.
    To avoid that, we want lookup tables between utf-8 bytes and unicode strings.
    And avoids mapping to whitespace/control characters the bpe code barfs on.
    """
    bs = list(range(ord("!"), ord("~")+1))+list(range(ord("¡"), ord("¬")+1))+list(range(ord("®"), ord("ÿ")+1))
    cs = bs[:]
    n = 0
    for b in range(2**8):
        if b not in bs:
            bs.append(b)
            cs.append(2**8+n)
            n += 1
    cs = [chr(n) for n in cs]
    return dict(zip(bs, cs))


def get_pairs(word):
    """Return set of symbol pairs in a word.
    Word is represented as tuple of symbols (symbols being variable-length strings).
    """
    pairs = set()   # NOTE: this is set rather than list
    prev_char = word[0]
    for char in word[1:]:
        pairs.add((prev_char, char))
        prev_char = char
    return pairs


def basic_clean(text):
    text = ftfy.fix_text(text)
    text = html.unescape(html.unescape(text))
    return text.strip()


def whitespace_clean(text):
    text = re.sub(r'\s+', ' ', text)
    text = text.strip()
    return text


class SimpleTokenizer(object):
    def __init__(self, bpe_path: str = "bpe_simple_vocab_16e6.txt.gz", context_length = 77):
        self.byte_encoder = bytes_to_unicode()
        self.byte_decoder = {v: k for k, v in self.byte_encoder.items()}
        # merges: ['o n', 'e n', 'o n</w>', 'a l', 'a t', 'e r</w>', 'i t', 'i n</w>']
        merges = gzip.open(bpe_path).read().decode("utf-8").split('\n')
        merges = merges[1:49152-256-2+1]
        merges = [tuple(merge.split()) for merge in merges]
        vocab = list(bytes_to_unicode().values())
        vocab = vocab + [v+'</w>' for v in vocab]
        for merge in merges:
            vocab.append(''.join(merge))
        vocab.extend(['<|startoftext|>', '<|endoftext|>'])
        self.encoder = dict(zip(vocab, range(len(vocab))))
        self.decoder = {v: k for k, v in self.encoder.items()}
        self.bpe_ranks = dict(zip(merges, range(len(merges))))
        self.cache = {'<|startoftext|>': '<|startoftext|>', '<|endoftext|>': '<|endoftext|>'}
        self.pat = re.compile(r"""<\|startoftext\|>|<\|endoftext\|>|'s|'t|'re|'ve|'m|'ll|'d|[\p{L}]+|[\p{N}]|[^\s\p{L}\p{N}]+""", re.IGNORECASE)
        self.context_length = context_length

    def bpe(self, token):
        """ Decompose a word into tokens: "tigering" -> ["tiger", "ing</w>"]

        This function makes use of BPE decoding. The bpe_simple_vocab_16e6... file has
        already contained the tokenized vocabs that building fuller words; and that
        file also has organized the components in the right order so that it
        decodes nicely (if you shuffle the order of `merges`, the encoded token
        will not be correct).
        """
        if token in self.cache:
            return self.cache[token]
        word = tuple(token[:-1]) + ( token[-1] + '</w>',)
        pairs = get_pairs(word)

        if not pairs:
            return token+'</w>'

        while True:
            bigram = min(pairs, key = lambda pair: self.bpe_ranks.get(pair, float('inf')))
            if bigram not in self.bpe_ranks:
                break
            first, second = bigram
            new_word = []
            i = 0
            while i < len(word):
                try:
                    j = word.index(first, i)
                    new_word.extend(word[i:j])
                    i = j
                except:
                    new_word.extend(word[i:])
                    break

                if word[i] == first and i < len(word)-1 and word[i+1] == second:
                    new_word.append(first+second)
                    i += 2
                else:
                    new_word.append(word[i])
                    i += 1
            new_word = tuple(new_word)
            word = new_word
            if len(word) == 1:
                break
            else:
                pairs = get_pairs(word)
        word = ' '.join(word)
        self.cache[token] = word
        return word

    def _encode(self, text):
        """
        Encoding steps:
            1. Clean the text string
            2. Retrieve each `word`
            3. For each word:
                a. Clean up the word
                b. Perform BPE tokenization (e.g. 'tigering' -> 'tiger ing</w>')
                c. Substitle the token to index
            -> Result: a list of tokens representing the whole sentence

        # Args:
            text <str>: a string (e.g "a tiger")
        # Returns
            <[int]>: list of tokens index
        """
        bpe_tokens = []
        text = whitespace_clean(basic_clean(text)).lower()
        for token in re.findall(self.pat, text):
            token = ''.join(self.byte_encoder[b] for b in token.encode('utf-8'))
            bpe_tokens.extend(self.encoder[bpe_token] for bpe_token in self.bpe(token).split(' '))
        return bpe_tokens

    def decode(self, tokens):
        text = ''.join([self.decoder[token] for token in tokens])
        text = bytearray([self.byte_decoder[c] for c in text]).decode('utf-8', errors="replace").replace('</w>', ' ')
        return text

    def encode(self, text: list):
        """
        # Args
            text <[str]>: each string is an input
        # Returns
            <tensor>: shape (len(text) x context_length) (each element is an index)
            Example:
                tensor([[  320, 22697, 49407,     0,     0,     0,...],
                        [  320,  1929, 49407,     0,     0,     0,...]])
        """
        text_tokens = [self._encode("<|startoftext|> " + desc + " <|endoftext|>") for desc in text]
        text_input = torch.zeros(len(text_tokens), self.context_length, dtype=torch.long)

        for i, tokens in enumerate(text_tokens):
            # truncate and keep EOS unremoved
            length = min(len(tokens), self.context_length)
            tokens = torch.tensor(tokens[:length-1]+tokens[-1:])
            text_input[i, :length] = tokens
        return text_input

if __name__ == '__main__':
    tokenizer = SimpleTokenizer()
    # query = ['tigering a diagram', 'a dog', 'a cat', 'a tiger', 'a tigering', 'some very long text about tiger']
    query = ['tigering a diagram', 'a tiger']
    text = tokenizer.encode(query)
    print(text)
