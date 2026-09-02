# Criando um tema novo

Copie um dos arquivos existentes (`light.theme`, `dark.theme` ou
`solarized.theme`), renomeie para `<nome>.theme` e ajuste as cores. O nome do
arquivo é o "código" interno do tema; `_meta_name` é o que aparece no seletor
da interface.

Todo valor de cor é uma string hex (`"#rrggbb"`). Se uma chave faltar, o
programa usa o valor equivalente do tema "Light" embutido — então dá pra
criar um `.theme` só com as chaves que você quer mudar.

| Chave                       | Onde aparece                                                        |
|------------------------------|-----------------------------------------------------------------------|
| `background_color`           | Fundo da janela e dos painéis (Origem, Progresso, Log)                |
| `text_color`                 | Texto normal (labels, títulos dos painéis)                            |
| `secondary_text_color`       | Texto secundário: linha "Tamanho / Range / Tipo" e as estatísticas    |
| `border_color`                | Contorno de painéis, botões, campos de texto e barra de progresso     |
| `button_background_color`    | Fundo dos botões e das setinhas do spinbox/combobox                   |
| `button_text_color`          | Texto dos botões                                                       |
| `input_background_color`     | Fundo do campo de URL, "Salvar como", workers e da caixa do combobox   |
| `input_text_color`           | Texto digitado nesses campos                                          |
| `accent_color`                | Preenchimento da barra de progresso e destaque ao passar o mouse/focar botões |
| `log_background_color`       | Fundo do painel de log (a caixa preta na parte de baixo)               |
| `log_text_color`             | Texto do painel de log                                                 |

Dica: mantenha um bom contraste entre `input_background_color` /
`input_text_color` e entre `log_background_color` / `log_text_color` —
são os dois lugares com mais texto na tela.
