#!/usr/bin/env python

#2014/09/30
#2015/08/12 added options; changed scoring: /ObjStm 0.75; obj/endobj or stream/endstream discrepancy: 0.50
#2015/08/13 added instructions
#2017/10/29 added /URI

class cPDFiDTriage(cPluginParent):
    """
    Modified by CSE to fit ASSEMBLYLINE Service
    """
    onlyValidPDF = False
    name = 'Triage plugin'

    def __init__(self, oPDFiD):
        self.oPDFiD = oPDFiD
        self.hits = set()

    def Score(self):
        """
        Modified by CSE to fit ASSEMBLYLINE Service
        """
        score = 0
        # Javascript - separated so we do not double-score
        if '/JS' in self.oPDFiD.keywords and self.oPDFiD.keywords['/JS'].count > 0:
            self.hits.add('/JS')
        if '/JavaScript' in self.oPDFiD.keywords and self.oPDFiD.keywords['/JavaScript'].count > 0:
            self.hits.add('/JavaScript')
        if self.oPDFiD.keywords['/JavaScript'].count > 0 or self.oPDFiD.keywords['/JS'].count > 0:
            score += 100
        for keyword in ('/JBIG2Decode', '/Colors > 2^24'):
            if keyword in self.oPDFiD.keywords and self.oPDFiD.keywords[keyword].count > 0:
                self.hits.add(keyword)
                score += 50
        # Auto open/Launch - separated so we do not double-score
        if '/AA' in self.oPDFiD.keywords and self.oPDFiD.keywords['/AA'].count > 0:
            self.hits.add('/AA')
        if '/OpenAction' in self.oPDFiD.keywords and self.oPDFiD.keywords['/OpenAction'].count > 0:
            self.hits.add('/OpenAction')
        if '/Launch' in self.oPDFiD.keywords and self.oPDFiD.keywords['/Launch'].count > 0:
            self.hits.add('/Launch')
        if self.oPDFiD.keywords['/AA'].count > 0 or self.oPDFiD.keywords['/OpenAction'].count > 0 \
                or self.oPDFiD.keywords['/Launch'].count > 0:
            score += 50
        # Forms, Flash, XFA
        for keyword in ('/AcroForm', '/RichMedia', '/XFA'):
            if keyword in self.oPDFiD.keywords and self.oPDFiD.keywords[keyword].count > 0:
                self.hits.add(keyword)
                score += 25
        # Encrypted content
        for keyword in ['/Encrypt']:
            if keyword in self.oPDFiD.keywords and self.oPDFiD.keywords[keyword].count > 0:
                self.hits.add(keyword)
                score += 25
        # Other content to flag for PDFParser to extract, but not to score
        for keyword in ['/Annot']:
            if keyword in self.oPDFiD.keywords and self.oPDFiD.keywords[keyword].count > 0:
                self.hits.add(keyword)
                score += 1
        for keyword in ('/ObjStm', ):
            if keyword in self.oPDFiD.keywords and self.oPDFiD.keywords[keyword].count > 0:
                self.hits.add(keyword)
                score += 1
        for keyword in ['/URI']:
            if keyword in self.oPDFiD.keywords and self.oPDFiD.keywords[keyword].count > 0:
                self.hits.add(keyword)
                score += 1
        return score, self.hits

    def Instructions(self, score, hits):
        """
        Modified by CSE to fit ASSEMBLYLINE Service
        Description information taken from https://blog.didierstevens.com/programs/pdf-tools/
        """
        instruct = {
            '/JS': '"/JS": indicating javascript is present in the file.\n',
            '/JavaScript': '"/JavaScript": indicating javascript is present in the file.\n',
            '/AA': '"/AA": indicating automatic action to be performed when the page/document is viewed.\n',
            '/Annot': '"/Annot": sample contains annotations. '
                      'Not suspicious but should be examined if other signs of maliciousness present.\n',
            '/OpenAction': '"/OpenAction": indicating automatic action to be performed when the page/document '
                           'is viewed."\n',
            '/AcroForm': '"/AcroForm": sample contains AcroForm object. These can be used to hide malicious code."\n',
            '/JBIG2Decode': '"/JBIG2Decode": indicating JBIG2 compression."\n',
            '/RichMedia': '"/RichMedia": indicating embedded Flash. \n',
            '/Launch': '"/Launch": counts launch actions.\n',
            '/Encrypt': '"/Encrypt": encrypted content in sample\n',
            '/XFA': '"/XFA": indicates XML Forms Architecture. These can be used to hide malicious code.\n',
            '/Colors > 2^24': '"/Colors > 2^24": hits when the number of colors is expressed with more than 3 bytes.\n',
            '/ObjStm': '"/ObjStm": sample contains object stream(s). Can be used to obfuscate objects.\n',
            '/URI': '"/URI": sample contains URLs.\n'
        }

        message = "The following keywords have been flagged in this sample:\n"
        for h in hits:
            message += "{}".format(instruct.get(h))

        if score > 0:
            return message

        return

AddPlugin(cPDFiDTriage)
