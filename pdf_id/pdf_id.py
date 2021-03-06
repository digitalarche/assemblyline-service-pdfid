from copy import deepcopy

from pdf_id.pdfid import pdfid
from pdf_id.pdfparser import pdf_parser
import hashlib
import os
import re
import unicodedata

from assemblyline.common.exceptions import NonRecoverableError
from assemblyline_v4_service.common.balbuzard.patterns import PatternMatch
from assemblyline_v4_service.common.base import ServiceBase
from assemblyline_v4_service.common.result import Result, ResultSection, BODY_FORMAT, Heuristic


class PDFId(ServiceBase):
    def __init__(self, config=None):
        super(PDFId, self).__init__(config)

    @staticmethod
    def get_pdfid(path, additional_keywords, options, deep):
        """Run PDFId code on sample.

        Args:
            path: Original PDF sample path.
            additional_keywords: List of additional keywords to be searched (provided in service configuration).
            options: List of PDFId module plugins (provided in service configuration)..
            deep: Boolean value of AL submission deep scan value.

        Returns:
            PDFId result and error list.
        """
        try:
            pdfid_result, errors = pdfid.PDFiDMain(path, additional_keywords, options, deep)
        except Exception as e:
            raise Exception("PDFID failed to run on sample. Error: {}" .format(e))

        return pdfid_result, errors

    def get_pdf_parser(self, path, working_dir, options):
        """Run PDF Parser code on sample.

        Args:
            path: Original PDF sample path.
            working_dir: AL working directory.
            options: Dictionary of PDFId module options (see pdf_parser.py)

        Returns:
            PDF Parser result and error list.
        """
        try:
            pdf_parser_statresult, errors = pdf_parser.PDFParserMain(path, working_dir, **options)
        except Exception as e:
            pdf_parser_statresult = None
            errors = []
            self.log.error("pdf_parser failed to run on sample. Error: {}" .format(e))

        return pdf_parser_statresult, errors

    # noinspection PyBroadException
    def analyze_pdf(self, request, res_txt, path, working_dir, heur, additional_keywords, get_malform=True):
        """Extract metadata, keyword objects and content of interest from a PDF sample using PDFId, PDFId plugins,
        and PDF Parser.

        Args:
            request: AL request object.
            res_txt: Header string for AL result section title.
            path: Original PDF sample path.
            working_dir: AL working directory.
            heur: List of plugins to run on PDFId results (provided in service configuration).
            additional_keywords: List of additional keywords to be searched (provided in service configuration).
            get_malform: Extract malformed objects from PDF.

        Returns:
            AL result object, AL heuristics list to add to result, list of object streams (objstms), and an errors list.
        """
        triage_keywords = set()
        all_errors = set()
        embed_present = False
        objstms = False
        res = ResultSection(title_text=res_txt)
        carved_extracted_shas = set()

        if request.deep_scan:
            run_pdfparse = True
        else:
            run_pdfparse = False

        # Run PDFId
        try:
            pdfid_result, errors = self.get_pdfid(path, additional_keywords, heur, request.deep_scan)
        except Exception as e:
            raise NonRecoverableError(e)
        # Parse PDFId results
        pdfidres = ResultSection(title_text="PDFID Results", parent=res)
        if len(pdfid_result) == 0:
            pdfidres.add_line("No results generated for file. Please see errors.")
        else:
            # Do not run for objstms, which are being analyzed when get_malform == False
            if get_malform:
                version = pdfid_result.get("PDFID", None)
                if version:
                    pdfidres.add_line(version[0])
                properties = pdfid_result.get("Properties", None)
                if properties:
                    pres = ResultSection(title_text="PDF Properties", parent=pdfidres)
                    for plist in properties:
                        pres.add_line("{0}: {1}" .format(plist[0], plist[1]))
                        if plist[0] == "/ModDate":
                            pres.add_tag('file.pdf.date.modified', plist[1])
                        elif plist[0] == "/CreationDate":
                            pres.add_tag('file.date.creation', plist[1])
                        elif plist[0] == "/LastModified":
                            pres.add_tag('file.date.last_modified', plist[1])
                        elif plist[0] == "/SourceModified":
                            pres.add_tag('file.pdf.date.source_modified', plist[1])
                        elif plist[0] == "/pdfx":
                            pres.add_tag('file.pdf.date.pdfx', plist[1])
                entropy = pdfid_result.get("Entropy", None)
                if entropy:
                    enres = ResultSection(title_text="Entropy", parent=pdfidres)
                    for enlist in entropy:
                        enres.add_line("{0}: {1}, ({2})" .format(enlist[0], enlist[1], enlist[2]))
            flags = pdfid_result.get("Flags", None)
            if flags:
                fres = ResultSection(title_text="PDF Keyword Flags", parent=pdfidres)
                for flist in flags:
                    if flist[0] == "/ObjStm":
                        objstms = True
                    if len(flist) == 3:
                        fres.add_line("{0}:Count: {1}, Hex-Encoded Count: {2}" .format(flist[0], flist[1], flist[2]))
                    else:
                        fres.add_line("{0}:Count: {1}".format(flist[0], flist[1]))
                    fres.add_tag('file.string.extracted', flist[0].replace("/", "", 1))
                    if flist[0] in additional_keywords:
                        triage_keywords.add(flist[0].replace("/", "", 1))

            plugin = pdfid_result.get("Plugin", [])

            # If any plugin results, or flagged keywords found, run PDF Parser
            if plugin or len(triage_keywords) > 0:
                run_pdfparse = True

            for pllist in plugin:
                pl_name, pl_heur, pl_text = pllist
                pl_heur = int(pl_heur)
                pl_text = pl_text[14:]
                if not pl_text or pl_text == "None":
                    continue

                if pl_name in ['EmbeddedFile', 'Name Obfuscation']:
                    modres = ResultSection(title_text=pl_text, parent=pdfidres)

                    if pl_heur > 0:
                        modres.set_heuristic(pl_heur)

                    if pl_name == 'EmbeddedFile':
                        embed_present = True

                elif pl_name in ['Triage', 'Suspicious Properties']:
                    javascript_found = False
                    for line in pl_text.splitlines():
                        lineres = ResultSection(title_text=line)
                        # Triage results
                        if '/JavaScript' in line:
                            triage_keywords.add('JavaScript')
                            if not javascript_found:
                                lineres.set_heuristic(19)
                                javascript_found = True
                        elif '/JS' in line:
                            triage_keywords.add('JS')
                            if not javascript_found:
                                lineres.set_heuristic(19)
                                javascript_found = True
                        elif '/JBIG2Decode' in line:
                            triage_keywords.add('JBIG2Decode')
                            lineres.set_heuristic(3)
                        elif '/Colors > 2^24' in line:
                            triage_keywords.add('Colors > 2^24')
                            lineres.set_heuristic(20)
                        elif '/AA' in line:
                            triage_keywords.add('AA')
                            lineres.set_heuristic(1)
                        elif '/Launch' in line:
                            triage_keywords.add('Launch')
                            lineres.set_heuristic(1)
                        elif '/OpenAction' in line:
                            triage_keywords.add('OpenAction')
                            lineres.set_heuristic(1)
                        elif '/GoToE' in line:
                            triage_keywords.add('GoToE')
                            lineres.set_heuristic(21)
                        elif '/GoToR' in line:
                            triage_keywords.add('GoToR')
                            lineres.set_heuristic(22)
                        elif '/Encrypt' in line:
                            triage_keywords.add('Encrypt')
                            lineres.set_heuristic(11)
                        elif '/AcroForm' in line:
                            triage_keywords.add('AcroForm')
                            lineres.set_heuristic(4)
                        elif '/RichMedia' in line:
                            triage_keywords.add('RichMedia')
                            lineres.set_heuristic(5)
                        elif '/XFA' in line:
                            triage_keywords.add('XFA')
                            lineres.set_heuristic(23)
                        elif '/Annot' in line:
                            triage_keywords.add('Annot')
                            lineres.set_heuristic(25)
                        elif '/ObjStm' in line:
                            triage_keywords.add('ObjStm')
                            lineres.set_heuristic(7)
                        elif '/URI' in line:
                            triage_keywords.add('URI')
                            lineres.set_heuristic(24)

                        # Suspicious properties results
                        elif "eof2" in line:
                            lineres.set_heuristic(2)
                        elif "eof5" in line:
                            lineres.set_heuristic(17)
                        elif "page" in line:
                            lineres.set_heuristic(26)
                        elif "entropy" in line:
                            lineres.set_heuristic(12)
                        elif "obj/endobj" in line:
                            lineres.set_heuristic(13)
                        elif "stream/endstream" in line:
                            lineres.set_heuristic(14)

                        if lineres.heuristic is not None:
                            pdfidres.add_subsection(lineres)

        for e in errors:
            all_errors.add(e)
            if e.startswith('Error running plugin'):
                self.log.warn(e)

        if run_pdfparse:
            # CALL PDF parser and extract further information
            pdf_parserres = ResultSection(title_text="PDF Parser Results")
            # STATISTICS
            # Do not run for objstms, which are being analyzed when get_malform == False
            if get_malform:
                options = {
                    "stats": True,
                }
                pdf_parser_result, errors = self.get_pdf_parser(path, working_dir, options)

                if pdf_parser_result:
                    if len(pdf_parser_result) == 0:
                        pdf_parserres.add_line("No statistical results generated for file. Please see errors.")
                    else:
                        version = pdf_parser_result.get("version", None)
                        if version and version[0] != '0':
                            pdf_parserres.add_line(version[0])
                        stats = pdf_parser_result.get("stats", None)
                        if stats:
                            sres = ResultSection(title_text="PDF Statistcs", parent=pdf_parserres,
                                                 body_format=BODY_FORMAT.MEMORY_DUMP)
                            for p in stats:
                                sres.add_line(p)
                    for e in errors:
                        all_errors.add(e)

            # Triage plugin -- search sample for keywords and carve content or extract object (if it contains a stream)
            carved_content = {}  # Format { "objnum": [{keyword: content list}}
            obj_extract_triage = set()
            jbig_objs = set()

            for keyword in triage_keywords:
                # ObjStms handled differently
                if keyword == 'ObjStm':
                    continue

                options = {
                    "search": keyword,
                }
                pdf_parser_result, errors = self.get_pdf_parser(path, working_dir, options)

                if pdf_parser_result:
                    for p in pdf_parser_result['parts']:
                        content = ""
                        references = []
                        # Trailer will be extracted anyways, try and grab all references anyways -- will be messy
                        if p.startswith("trailer:"):
                            # Grab the content after the keyword
                            # Check that keyword actually in content
                            if "/{}" .format(keyword) in p:
                                try:
                                    content = p.split(keyword, 1)[1].replace('>>++>>', '').split("/", 1)[0].strip()
                                    references = re.findall("[0-9]* [0-9]* R", content)
                                except Exception:
                                    continue
                        # If not trailer, should be object
                        elif 'Referencing:' in p:
                            # Grab the content after the keyword
                            if '>>++>>' in p:
                                try:
                                    content = p.split(keyword, 1)[1].replace('>>++>>', '').strip()
                                except Exception:
                                    try:
                                        content = p.split("\n", 3)[3]
                                    except Exception:
                                        content = p
                            else:
                                try:
                                    content = p.split("\n", 3)[3]
                                except Exception:
                                    content = p
                            # Sometimes the content is the same keyword with references (i.e "/URI /URI 10 0 R"
                            if content.startswith("/{}" .format(keyword)):
                                try:
                                    content = re.sub("/{}[ ]*" .format(keyword), "", content, 1)
                                except Exception:
                                    pass
                            try:
                                references = p.split("\n", 3)[2].replace('Referencing:', '').strip().split(", ")
                            except Exception:
                                pass
                        # Only extract JBIG2Decode objects with deep scan, but always report on their presence
                        if keyword == "JBIG2Decode" and "/Filter" in p and "Contains stream" in p:
                            try:
                                objnum = p.split("\n", 1)[0].split(" ")[1]
                                if request.deep_scan:
                                    obj_extract_triage.add(objnum)
                                jbig_objs.add(objnum)
                                continue
                            except Exception as e:
                                self.log.debug(e)
                                continue
                        # If no content, then keyword likely points to reference objects, so grab those
                        if content == '':
                            if len(references) > 0:
                                content = references
                            else:
                                # Something is wrong, drop it.
                                continue
                        else:
                            while True:
                                # Multiple references might be in a list, i.e. /Annot # # R vs. /Annots [# # R # # R]
                                islist = re.match(r"[s]?[ ]?\[([0-9]* [0-9]* R[ \\rn]{0,8})*\]", content)
                                if islist:
                                    content = re.sub(r"[\[\]]", "", islist.group(0).replace("s ", '')
                                                     .replace("R ", "R,")).split(",")
                                    break
                                # References might be with instructions, i.e. [# # R /FitH null]
                                withinst = re.match(r"[s]?[ \\']{0,3}\[[ ]?([0-9]* [0-9]* R)[ \\rn]{1,8}"
                                                    r"[/a-zA-Z0-9 ]*[ ]?\]", content)
                                if withinst:
                                    content = [withinst.group(1)]
                                    break
                                content = [content]
                                break
                        for c in content:
                            # If keyword = Javascript and content starts with '/JS', disregard as 'JS' will be extracted
                            if "JS" in triage_keywords and keyword == "JavaScript" and "/JS" in c[0:5]:
                                continue
                            if c in references or re.match("[0-9]* [0-9]* R", c):
                                try:
                                    ref_obj = c.split(" ", 1)[0]
                                    options = {
                                        "object": ref_obj,
                                        "get_object_detail": True
                                    }
                                    pdf_parser_subresult, err = self.get_pdf_parser(path, working_dir, options)

                                    if pdf_parser_subresult:
                                        for sub_p in pdf_parser_subresult['parts']:
                                            sub_references = sub_p.split("\n", 3)[2].replace('Referencing:', '')\
                                                .strip().split(", ")
                                            ptyp = sub_p.split("\n", 2)[1].replace('Type:', '').strip().replace("/", "")
                                            # If the object contains a stream, extract the object.
                                            if "Contains stream" in sub_p:
                                                try:
                                                    objnum = sub_p.split("\n", 1)[0].split(" ")[1]
                                                    obj_extract_triage.add(objnum)
                                                except Exception:
                                                    pass
                                            # Or if the object Type is the keyword, grab all referenced objects.
                                            elif sub_references[0] != '' and len(sub_references) >= 1 \
                                                    and ptyp == keyword:
                                                for sr in sub_references:
                                                    try:
                                                        objnum = sr.split(" ", 1)[0]
                                                        obj_extract_triage.add(objnum)
                                                    except Exception:
                                                        pass
                                            # If not, extract object detail in to carved output
                                            elif pdf_parser_subresult['obj_details'] != "":
                                                try:
                                                    objnum = sub_p.split("\n", 1)[0].split(" ")[1]
                                                    if objnum in carved_content:
                                                        carved_content[objnum]\
                                                            .append({keyword: pdf_parser_subresult['obj_details']})
                                                    else:
                                                        carved_content[objnum] = \
                                                            [{keyword: pdf_parser_subresult['obj_details']}]
                                                except Exception:
                                                    continue

                                    for e in err:
                                        errors.add(e)
                                except Exception:
                                    # If none of that work, just extract the original object for examination.
                                    try:
                                        objnum = p.split("\n", 1)[0].split(" ")[1]
                                        obj_extract_triage.add(objnum)
                                    except Exception:
                                        pass
                            # If content does not look like a reference:
                            else:
                                if p.startswith("trailer:"):
                                    continue
                                objnum = p.split("\n", 1)[0].split(" ")[1]
                                # If the object contains a stream extract the object
                                if p.split("\n", 4)[3] == "Contains stream":
                                    obj_extract_triage.add(objnum)
                                else:
                                    # Or just carve the content
                                    if objnum in carved_content:
                                        carved_content[objnum].append({keyword: c})
                                    else:
                                        carved_content[objnum] = [{keyword: c}]

                    for e in errors:
                        all_errors.add(e)

            # Add carved content to result output
            show_content_of_interest = False
            if len(carved_content) > 0 or len(jbig_objs) > 0:
                carres = ResultSection(title_text="Content of Interest")
            else:
                carres = None

            if len(jbig_objs) > 0:
                jbigres = ResultSection(title_text="The following Object IDs are JBIG2DECODE streams:",
                                        body_format=BODY_FORMAT.MEMORY_DUMP, parent=carres)
                jbigres.add_line(', '.join(map(str, jbig_objs)))
                show_content_of_interest = True

            if len(carved_content) > 0:
                for k, l in sorted(carved_content.items()):
                    for d in l:
                        for keyw, con in d.items():
                            subres = ResultSection(title_text="Object {0}: Hits for Keyword '{1}':"
                                                   .format(k, keyw))
                            subres.set_heuristic(8)

                            con_bytes = con.encode()
                            if len(con) < 500:
                                subres.body_format = BODY_FORMAT.MEMORY_DUMP
                                subres.add_line(con)

                                # Check for IOC content
                                patterns = PatternMatch()
                                st_value = patterns.ioc_match(con_bytes, bogon_ip=True)
                                if len(st_value) > 0:
                                    carres.add_subsection(subres)
                                    show_content_of_interest = True
                                    for ty, val in st_value.items():
                                        if val == "":
                                            asc_asc = unicodedata.normalize('NFKC', val).encode('ascii', 'ignore')
                                            subres.add_tag(ty, asc_asc)
                                        else:
                                            ulis = list(set(val))
                                            for v in ulis:
                                                subres.add_tag(ty, v)
                            else:
                                crv_sha = hashlib.sha256(con_bytes).hexdigest()

                                if crv_sha not in carved_extracted_shas:
                                    f_name = "carved_content_obj_{}_{}".format(k, crv_sha[0:7])
                                    subres.add_lines(["Content over 500 bytes it will be extracted for analysis",
                                                      "Name: {} - SHA256: {}".format(f_name, crv_sha)])
                                    carres.add_subsection(subres)
                                    show_content_of_interest = True
                                    crvf = os.path.join(self.working_directory, f_name)
                                    with open(crvf, 'wb') as f:
                                        f.write(con_bytes)
                                    request.add_extracted(crvf, os.path.basename(crvf),
                                                          "Extracted content from object {}" .format(k))
                                    carved_extracted_shas.add(crv_sha)

            if show_content_of_interest:
                pdf_parserres.add_subsection(carres)

            # ELEMENTS
            # Do not show for objstms
            if get_malform:
                if request.deep_scan:
                    options = {
                        "verbose": True,
                        "nocanonicalizedoutput": True,
                        "get_malform": get_malform
                    }
                elif embed_present:
                    options = {
                        "verbose": True,
                        "elements": "ctsi",
                        "type": "/EmbeddedFile",
                        "get_malform": get_malform
                    }
                else:
                    options = {
                        "verbose": True,
                        "elements": "cst",
                        "get_malform": get_malform
                    }
                pdf_parser_result, errors = self.get_pdf_parser(path, working_dir, options)

                embed_extracted = set()
                if pdf_parser_result:
                    if len(pdf_parser_result) == 0:
                        pdf_parserres.add_line("No structure information generated for file. Please see errors.")
                    else:
                        # PDF Parser will write any malformed content over 100 bytes to a file
                        files = pdf_parser_result.get("files", None)
                        if files:
                            for f, l in files.items():
                                if f == 'malformed':
                                    if len(l) > 0:
                                        pdf_parserres.set_heuristic(6)
                                    for i in l:
                                        request.add_extracted(i, os.path.basename(i),
                                                              "Extracted malformed content in PDF Parser Analysis.")

                        parts = pdf_parser_result.get("parts", None)
                        # Extract service will extract the sample's embedded files.
                        # However we want to make note of them so that they are not extracted again below
                        if parts:
                            for p in sorted(parts):
                                if "Type: /EmbeddedFile" in p:
                                    getobj = p.split("\n", 1)[0].split(" ")[1]
                                    embed_extracted.add(getobj)

                # Extract objects collected from above analysis
                obj_to_extract = obj_extract_triage - embed_extracted - jbig_objs

                if len(obj_to_extract) > 0:
                    options = {
                        "filter": True,
                        "object": obj_to_extract,
                        "dump": "extracted_obj_",
                    }
                    pdf_parser_result, errors = self.get_pdf_parser(path, working_dir, options)

                    if pdf_parser_result:
                        files = pdf_parser_result.get("files", None)
                        extracted_files = []
                        if files:
                            for f, l in files.items():
                                if f == 'embedded':
                                    for i in l:
                                        f_name = os.path.basename(i)
                                        obj_id = f_name.replace("extracted_obj_", "")
                                        extracted_files.append("Extracted object {} as {}".format(obj_id, f_name))
                                        request.add_extracted(i, f_name,
                                                              "Object {} extracted in PDF Parser Analysis."
                                                              .format(obj_id))
                        for e in errors:
                            all_errors.add(e)

                        if extracted_files:
                            extract_res = ResultSection(title_text="Extracted embedded objects",
                                                        parent=pdf_parserres)
                            extract_res.set_heuristic(9)
                            extract_res.add_lines(extracted_files)

                # Extract jbig2decode objects in deep scan mode
                if request.deep_scan and len(jbig_objs) > 0:
                    options = {
                        "object": jbig_objs,
                        "dump": "extracted_jb_obj_",
                    }
                    pdf_parser_result, errors = self.get_pdf_parser(path, working_dir, options)

                    if pdf_parser_result:
                        extracted_jb = []
                        files = pdf_parser_result.get("files", None)
                        if files:
                            for f, l in files.items():
                                if f == 'embedded':
                                    for i in l:
                                        f_name = os.path.basename(i)
                                        obj_id = f_name.replace("extracted_jb_obj_", "")
                                        extracted_jb.append("JBIG2DECODE object {} extracted as {}".format(obj_id,
                                                                                                           f_name))
                                        request.add_extracted(i, f_name,
                                                              "JBIG2DECODE object {} extracted in PDF Parser Analysis."
                                                              .format(obj_id))

                        for e in errors:
                            all_errors.add(e)

                        if extracted_jb:
                            jbig_extract_res = ResultSection(title_text="Extracted JBIG2Decode objects",
                                                             parent=pdf_parserres)
                            jbig_extract_res.set_heuristic(9)
                            jbig_extract_res.add_lines(extracted_jb)

            if len(pdf_parserres.subsections) > 0:
                res.add_subsection(pdf_parserres)

        return res, objstms, all_errors

    def write_objstm(self, path, working_dir, objstm, objstm_path):
        """Write object stream (objstm) to file as a mock PDF.

        Args:
            path: Original PDF sample path.
            working_dir: AL working directory.
            objstm: Content of objstm.
            objstm_path: File path to write mock PDF.

        Returns:
            File path of objstm file if extraction successful, or None.
        """
        stream_present = False
        header = b"%PDF-1.6\x0A%Fake header created by AL PDFID service.\x0A"
        trailer = b"%%EOF\x0A"
        obj_footer = b"\rendobj\r"
        objstm_file = None

        options = {
            "object": objstm,
            "dump": objstm_path,
            "filter": True,
            "raw": True,
        }
        pdf_parser_subresult, err = self.get_pdf_parser(path, working_dir, options)

        if pdf_parser_subresult:
            for sub_p in pdf_parser_subresult['parts']:
                if sub_p.split("\n", 4)[3] == "Contains stream":
                    stream_present = True
            if stream_present:
                files = pdf_parser_subresult.get("files", None)
                if files:
                    for fi, l in files.items():
                        if fi == 'embedded' and len(l) > 0:
                            objstm_file = l[0]
                            with open(objstm_file, 'rb+') as f:
                                stream = f.read()
                                # Remove any extra content before objects
                                if not re.match(b"<<.*", stream):
                                    extra_content = re.match(br'[^<]*', stream).group(0)
                                    stream = stream.replace(extra_content, b"%" + extra_content + b"\x0A")
                                obj_idx = 1
                                # Find all labels and surround them with obj headers
                                for lab in re.findall(rb"(<<[^\n]*>>[\x0A\x0D]|<<[^\n]*>>$)", stream):
                                    stream = stream.replace(lab, str(obj_idx).encode() + b" 0 obj\r" +
                                                            b"".join(lab.rsplit(b'\n', 1)) + obj_footer)
                                    obj_idx += 1
                                # Find all streams and surround them wirh stream headers
                                for ste in re.findall(rb">>(?:(?!stream)(?![\r\n]endobj)[^<])+", stream):
                                    # Might be multi-layer stream:
                                    if ste.endswith(b'>>'):
                                        continue
                                    stream = stream.replace(ste, b">>stream\n" + ste.replace(b'>>', b"", 1)
                                                            + b"\nendstream\rendobj\r")
                                # Find all labels with attached stream, and surround them with obj headers
                                for lab_ste in re.findall(rb'(?:(?:(?!obj)...)|(?:endobj))([\r\n]<<(?:(?!endobj).)+)',
                                                          stream, re.DOTALL):
                                    stream = stream.replace(lab_ste, b"\r" + str(obj_idx).encode() +
                                                            b" 0 obj\r" + lab_ste[2:])
                                    obj_idx += 1
                                f.seek(0, 0)
                                f.write(header + stream + trailer)

        return objstm_file

    def analyze_objstm(self, path, working_dir, deep_scan):
        """Extract object streams (objstm) from PDF sample and write to file as a mock PDF.

        Args:
            path: Original PDF sample path.
            working_dir: AL working directory.
            deep_scan: Boolean value of AL submission deep scan value.

        Returns:
            List of extracted objstm file paths.
        """
        objstm_extracted = set()

        obj_files = set()

        # Only extract first 2 if not deep scan
        if deep_scan:
            max_obst = 100
        else:
            max_obst = 1  # Really 2
        options_objstm = {
            "elements": "i",
            "type": "/ObjStm",
            "max_objstm": max_obst
        }

        pdf_parser_result, errors = self.get_pdf_parser(path, working_dir, options_objstm)
        if pdf_parser_result is None:
            parts = None
        else:
            parts = pdf_parser_result.get("parts", None)

        if parts:
            idx = 0
            for p in sorted(parts):
                if "Type: /ObjStm" in p:
                    getobj = p.split("\n", 1)[0].split(" ")[1]
                    if getobj in objstm_extracted:
                        continue
                    dump_file = os.path.join(self.working_directory, "objstm_{0}_{1}" .format(getobj, idx))
                    idx += 1
                    obj_file = self.write_objstm(path, working_dir, getobj, dump_file)
                    if obj_file:
                        objstm_extracted.add(getobj)
                        obj_files.add(obj_file)

        return obj_files

    def execute(self, request):
        """Main Module. See README for details."""
        max_size = self.config.get('MAX_PDF_SIZE', 3000000)
        request.result = result = Result()
        if (os.path.getsize(request.file_path) or 0) < max_size or request.deep_scan:
            path = request.file_path
            working_dir = self.working_directory

            # CALL PDFID and identify all suspicious keyword streams
            additional_keywords = self.config.get('ADDITIONAL_KEYS', [])
            heur = deepcopy(self.config.get('HEURISTICS', []))
            all_errors = set()

            res_txt = "Main Document Results"
            res, contains_objstms, errors = self.analyze_pdf(request, res_txt, path, working_dir,
                                                             heur, additional_keywords)
            result.add_section(res)

            for e in errors:
                all_errors.add(e)

            #  ObjStms: Treat all ObjStms like a standalone PDF document
            if contains_objstms:
                objstm_files = self.analyze_objstm(path, working_dir, request.deep_scan)
                obj_cnt = 1
                for osf in objstm_files:
                    parent_obj = os.path.basename(osf).split("_")[1]
                    res_txt = "ObjStream Object {0} from Parent Object {1}" .format(obj_cnt, parent_obj)
                    # It is going to look suspicious as the service created the PDF
                    heur = [x for x in heur if 'plugin_suspicious_properties' not in x
                            and 'plugin_embeddedfile' not in x and 'plugin_nameobfuscation' not in x]

                    res, contains_objstms, errors = self.analyze_pdf(request, res_txt, osf, working_dir, heur,
                                                                     additional_keywords, get_malform=False)

                    obj_cnt += 1
                    result.add_section(res)

            if len(all_errors) > 0:
                erres = ResultSection(title_text="Errors Analyzing PDF")
                for e in all_errors:
                    erres.add_line(e)
                result.add_section(erres)

        else:
            section = ResultSection("PDF Analysis of the file was skipped because the file is too big (limit is 3 MB).")
            section.set_heuristic(10)
            result.add_section(section)
