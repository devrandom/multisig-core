from setuptools import setup

setup(
    name='digitaloracle-pycoin',
    version='0.1',
    packages=[
        'digitaloracle',
        'digitaloracle.scripts',
    ],
    url='https://cryptocorp.co/api',
    license='http://opensource.org/licenses/MIT',
    author='devrandom',
    author_email='info@cryptocorp.co',
    entry_points={
        'console_scripts':
            [
                'digitaloracle = digitaloracle.scripts.digitaloracle:main',
                'decode_script = digitaloracle.scripts.decode_script:main',
            ]
    },
    description='The CryptoCorp digitaloracle API for pycoin ',
    classifiers=[
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.3',
        'Programming Language :: Python :: 3.4',
        'Topic :: Internet',
        'Topic :: Software Development :: Libraries :: Python Modules',
    ]
)
